# pyright: basic
"""파이프라인 P1 정렬: 스마트폰 사진 → 반듯하게 편 정면 이미지(warped) + QR 정보.

[이 파일의 역할]
  - 학생 답안지를 찍은 사진은 비스듬하고, 크기·기울기·원근이 사진마다 다름
  - 답안지 네 귀퉁이에 인쇄된 ArUco 마커(QR과 비슷한 흑백 사각형 표식)를 찾아서
    사진을 "스캐너로 스캔한 것처럼" 반듯한 정면 이미지로 펴 줌
  - 정면 이미지는 항상 같은 크기(200dpi A4)라서, 이후 단계는 layout 좌표(mm)만 알면
    칸 위치를 바로 계산할 수 있음 → 사진마다 칸을 따로 찾을 필요 없음

[파이프라인에서 위치]
  P1 정렬 (이 파일) → P2 시험 확인(flow.py) → P3 판독(read.py) → P4 채점(grading/score.py)
  → P5 엑셀(grading/excel.py)

[입력 → 출력]
  photo_bgr (사진, 크기·기울기 제각각) ──align()──► AlignResult
      .warped      : 200dpi 정면 이미지 (세로 1654x2339 / 가로 2339x1654 px) → P3 판독의 입력
      .qr_info     : {"exam_id", "template_id", ...} → P2 시험 확인의 입력
      .orientation : "portrait" / "landscape"
      .reproj_err_px, .blur_var : 정렬 품질 지표 (품질 검사·재촬영 기준 정할 때 사용)

[처리 순서]
  1. 흑백 변환, 흐림 정도 측정 (라플라시안 분산: 낮을수록 흐림. 지금은 기록만)
  2. ArUco 마커 검출(ID 0~3) → 마커 배치 모양으로 방향 판별 (세로·가로 양식이 같은 ID를 씀)
     마커 세트 2가지를 모두 찾아 봄 (frame.MARKER_SETS)
       - "team"  : 팀 양식·합성 데이터셋·우리 PDF (DICT_6X6_50, BL=2 BR=3)
       - "print" : 실제 인쇄해서 쓴 답안지 (DICT_4X4_50, BR=2 BL=3, 마커 16mm)
     → 세트·방향 조합마다 변환을 구해 보고 가장 잘 맞는 조합을 씀
  3. 사진 속 마커 코너 ↔ frame.py에 저장된 코너 좌표 짝짓기 (마커당 4점)
  4. 호모그래피(원근 변환 행렬) 계산 — 코너 12~16점 + RANSAC(튀는 점 제외)
  5. warpPerspective로 정면 이미지로 펴기
  6. 펴진 이미지의 고정 위치에서 QR 읽기 (실패하면 원본 사진 전체에서 한 번 더)

[왜 마커 중심 4점이 아니라 코너 16점?]
  - 점이 많을수록 오차가 평균되어 정확함
  - 마커 1개가 가려져도 나머지 3개(12점)로 정렬 가능 → 2개 이하면 재촬영 안내

[칸 잘라내기 crop_regions()]
  정렬 후에는 칸이 항상 layout 좌표 자리에 있음 → 사진마다 칸을 찾을 필요 없이 좌표로 바로 자름

[다른 파일과의 관계]
  - 이 파일을 부르는 쪽 : pipeline/flow.py(step_align), tools/smoke_test.py(align, crop_regions)
  - 이 파일이 부르는 쪽 : sheetgen/frame.py (마커 ID·위치·크기, 용지 크기, QR 위치 등 양식 고정값)
                          sheetgen/layout.py (mm→px 변환, QR 문자열 해석)
  - OpenCV(cv2)의 aruco 모듈(마커 검출), findHomography/warpPerspective(원근 보정),
    QRCodeDetector(QR 읽기)를 사용함

[주요 함수/클래스]
  - AlignError              : 정렬 불가(마커 부족 등)일 때 나는 예외 → 재촬영 안내
  - AlignResult             : 정렬 결과 묶음 (warped, H, 방향, 품질 지표, QR)
  - _detector()             : ArUco 마커 검출기 생성 (내부용)
  - _read_qr()              : QR 읽기 (OpenCV 내부 오류도 "못 읽음"으로 처리, 내부용)
  - align()                 : P1 메인 함수. 사진 → AlignResult
  - load_template_registry(): 폴더의 *.layout.json을 (양식 ID, 버전) → layout 사전으로 읽기
  - crop_regions()          : 정렬 이미지에서 칸별 작은 이미지 잘라내기 (CNN 학습·검수·OCR용)

[사용 예]
  import cv2
  from pipeline.align import align, AlignError
  photo = cv2.imread("student01.jpg")          # BGR 배열
  try:
      res = align(photo)
      cv2.imwrite("warped.png", res.warped)    # 반듯하게 편 답안지
      print(res.orientation, res.qr_info)      # 예: portrait {'exam_id': 'EX-...', ...}
  except AlignError as e:
      print("재촬영 필요:", e)
"""
from __future__ import annotations

import glob
import json
import os
from dataclasses import dataclass, field

import cv2
import numpy as np

from sheetgen import frame as F
from sheetgen.layout import bbox_to_px, mm_to_px, parse_qr_payload


class AlignError(RuntimeError):
    """정렬 불가 (마커 부족 등) → 재촬영 필요. 메시지는 학생 안내에 그대로 쓸 수 있게 작성.

    align()에서 다음 경우에 발생함.
      1. 우리 답안지 마커(ID 0~7)를 하나도 못 찾음
      2. 찾은 마커 수가 min_markers(기본 3개)보다 적음
      3. 호모그래피(원근 변환 행렬) 계산 실패
    flow.run_photo()가 이 예외를 잡아서 SheetResult.status = "align_failed"로 기록함.
    """


@dataclass
class AlignResult:
    """P1 정렬 결과. 다음 단계는 주로 warped와 qr_info만 씀.

    align()이 만들어서 돌려주고, flow.py의 SheetResult.align에 그대로 저장됨.
    필드 설명은 각 줄 오른쪽 주석 참고.
    """
    warped: np.ndarray                 # 정면으로 편 이미지 (BGR, 200dpi 기준 크기) → P3 판독 입력
    H: np.ndarray                      # 사진 좌표 → 정면 이미지 좌표 변환 행렬(3x3)
    orientation: str                   # "portrait" / "landscape"
    found_ids: list[int]               # 정렬에 쓴 마커 ID
    reproj_err_px: float               # 마커 코너를 H로 옮겼을 때 목표 위치와의 평균 거리(px). 크면 정렬 이상
    blur_var: float                    # 라플라시안 분산 (낮을수록 흐림). 재촬영 기준값은 실사진으로 정할 예정
    qr_text: str | None = None         # QR 원문
    qr_info: dict | None = None        # QR 해석 결과 (parse_qr_payload). 못 읽으면 None
    notes: list[str] = field(default_factory=list)   # 경고 메시지 (마커 일부 누락 등)
    marker_set: str = "team"           # 정렬에 쓴 마커 세트 ("team" 팀 양식 / "print" 실제 인쇄본)


def _detector(dict_name: str):
    """ArUco 검출기. 서브픽셀 코너 보정을 켜서 코너를 소수점 px 단위로 찾음 (정렬 정확도↑).

    마커 종류(사전)는 답안지를 만들 때 쓴 사전과 같아야 알아볼 수 있음
    → frame.MARKER_SETS의 세트마다 따로 만들어 씀.

    매개변수:
        dict_name: OpenCV ArUco 사전 이름 (예: "DICT_6X6_50", "DICT_4X4_50")
    반환:
        cv2.aruco.ArucoDetector 객체 (detectMarkers(gray)로 마커 검출에 사용)
    """
    d = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, dict_name))   # 마커 사전 (예: 6x6 비트, ID 0~49)
    p = cv2.aruco.DetectorParameters()                                               # 검출 세부 설정 (기본값 사용)
    p.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX                        # 코너를 1px보다 정밀하게 보정
    return cv2.aruco.ArucoDetector(d, p)


def _read_qr(qd, img) -> str:
    """QR 읽기. 못 읽으면 빈 문자열.

    OpenCV QR 검출기가 QR이 없는 사진 일부에서 내부 오류(cv2.error, convexHull)를 내는 경우가 있어서
    (합성 데이터셋의 빈 QR 칸 사진에서 확인) 오류도 '못 읽음'으로 처리함.

    매개변수:
        qd: cv2.QRCodeDetector 객체
        img: QR을 찾을 이미지 (BGR 배열)
    반환:
        QR 문자열 (못 읽으면 "")
    """
    try:
        text, _, _ = qd.detectAndDecode(img)
    except cv2.error:
        return ""
    return text or ""


def align(photo_bgr: np.ndarray, dpi: int = F.CANONICAL_DPI, min_markers: int = 3) -> AlignResult:
    """사진 → AlignResult. min_markers개 미만이면 AlignError.

    P1 메인 함수. 사진에서 마커를 찾아 방향을 정하고, 마커 코너를 기준점으로
    원근 변환을 계산해서 답안지를 정면 이미지로 펴고, QR까지 읽어 한 번에 돌려줌.

    매개변수:
        photo_bgr: 원본 사진 (OpenCV BGR 배열, 모양 (높이, 너비, 3). 예: cv2.imread("a.jpg") 결과)
        dpi: 정면 이미지 해상도 (기본 200 → A4 세로 1654x2339 px)
        min_markers: 정렬에 필요한 최소 마커 수 (기본 3개 = 코너 12점)
    반환:
        AlignResult (warped, H, orientation, found_ids, reproj_err_px, blur_var, qr_text, qr_info, notes)
        예: orientation="portrait", found_ids=[0, 1, 2, 3], reproj_err_px=0.4
    예외:
        AlignError: 우리 마커를 하나도 못 찾음 / 마커가 min_markers개 미만 / 호모그래피 계산 실패
    """
    gray = cv2.cvtColor(photo_bgr, cv2.COLOR_BGR2GRAY)          # 흑백 사진 (마커 검출·흐림 측정용)
    blur_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())   # 경계가 흐릴수록 값이 작아짐
    # 1) 마커 세트마다 마커를 찾고(ID 0~3만), 2) 방향마다 변환을 구해서 가장 잘 맞는 (세트, 방향)을 고름
    #    hits = [(위치, ID, 코너 4점)]. 위치는 "TL"(왼쪽 위) / "TR"(오른쪽 위) / "BR"(오른쪽 아래) / "BL"(왼쪽 아래)
    #    배경의 다른 마커 등 우리 ID가 아닌 것은 버리고, 같은 ID가 두 번 잡히면 첫 번째만 씀
    #    세로·가로 양식이 같은 ID를 쓰므로, 두 방향 각각의 '정면 기준 코너 위치'(dst)에 맞춰 변환을 구해 보고
    #    더 잘 맞는(재투영 오차가 작은) 쪽을 고름. 방향이 틀리면 정사각형 마커가 1.4배 늘어나야 해서 오차가 큼
    #    코너 순서(TL, TR, BR, BL)는 cv2.aruco와 frame.marker_corners_mm()이 같게 맞춰져 있어서 그대로 짝지음
    best = None          # (찾은 마커 수, -오차, 세트, 방향, 사진 코너, 목표 코너, 찾은 ID) — 마커 많고 오차 작은 쪽
    seen: dict[str, list[int]] = {}   # 세트 → 찾은 ID (실패 메시지용)
    for set_name, ms in F.MARKER_SETS.items():
        corners, ids, _ = _detector(ms["dictionary"]).detectMarkers(gray)   # corners: 마커별 (1,4,2) / ids: (N,1)
        hits, found = [], []
        if ids is not None:
            for c, i in zip(corners, ids.flatten()):
                pos = F.marker_position(int(i), set_name)    # 예: team에서 3 → "BR", 우리 마커 아니면 None
                if pos and int(i) not in found:
                    found.append(int(i))
                    hits.append((pos, int(i), c.reshape(4, 2)))
        seen[set_name] = sorted(found)
        if len(found) < min_markers:
            continue
        src_pts = np.asarray([p for _, _, pts in hits for p in pts], dtype=np.float32)   # 사진 속 코너 px (N, 2)
        for o in F.ORIENTATIONS:
            dst = [p for pos, _, _ in hits for p in np.array(F.marker_corners_mm(o, pos, set_name)) / 25.4 * dpi]
            dst_pts = np.asarray(dst, dtype=np.float32)
            H0, _ = cv2.findHomography(src_pts, dst_pts, 0)            # 최소제곱 (판별용, 이상치 제거 없음)
            if H0 is None:
                continue
            e = float(np.linalg.norm(cv2.perspectiveTransform(src_pts.reshape(-1, 1, 2), H0).reshape(-1, 2)
                                     - dst_pts, axis=1).mean())
            cand = (len(found), -e, set_name, o, src_pts, dst_pts, sorted(found))
            if best is None or cand[:2] > best[:2]:
                best = cand
    if best is None:
        n = max(len(v) for v in seen.values())
        if n == 0:
            raise AlignError("우리 답안지의 ArUco 마커를 찾지 못했습니다 (재촬영 필요)")
        ids_txt = sorted(max(seen.values(), key=len))
        raise AlignError(f"마커 {n}개만 검출됨 (필요 {min_markers}개): {ids_txt} — 네 귀퉁이가 모두 보이게 재촬영")
    _, _, marker_set, orientation, src_pts, dst_pts, found = best

    # 3) 호모그래피 (코너 12~16점 + RANSAC: 3px 넘게 어긋나는 점은 이상치로 제외)
    #    RANSAC을 쓰는 이유: 코너 하나가 잘못 잡혀도(손가락·반사광 등) 그 점 때문에 전체 변환이 틀어지지 않게
    H, _ = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 3.0)
    if H is None:
        raise AlignError("호모그래피 계산 실패")
    # 정렬 품질: 사진 속 코너를 H로 옮겼을 때 목표 위치와 얼마나 어긋나는지 (보통 1px 미만)
    proj = cv2.perspectiveTransform(src_pts.reshape(-1, 1, 2), H).reshape(-1, 2)   # H로 옮긴 코너 위치
    err = float(np.linalg.norm(proj - dst_pts, axis=1).mean())                      # 평균 거리(px)

    # 4) 정면 이미지로 펴기 (사진 밖 영역은 흰색으로 채움)
    w, h = F.canonical_size_px(orientation, dpi)   # 정면 이미지 가로·세로 px (세로 양식 200dpi면 1654, 2339)
    warped = cv2.warpPerspective(photo_bgr, H, (w, h), flags=cv2.INTER_LINEAR,
                                 borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
    res = AlignResult(warped, H, orientation, found, err, blur_var, marker_set=marker_set)
    if len(found) < 4:
        res.notes.append(f"마커 {4 - len(found)}개 누락 — 나머지로 정렬함")

    # 5) QR: 정렬된 이미지의 고정 위치에서 먼저, 실패하면 원본 사진 전체에서
    #    고정 위치를 먼저 보는 이유: 작은 영역만 보면 빠르고, 펴진 이미지라 QR이 반듯해서 잘 읽힘
    qd = cv2.QRCodeDetector()
    x, y, bw, bh = bbox_to_px(F.ORIENTATIONS[orientation]["qr_bbox_mm"], dpi)   # QR 영역 (왼쪽 위 x, y, 너비, 높이) px
    m = int(mm_to_px(3, dpi))   # QR 주변 3mm 여유를 두고 자름 (정렬 오차·여백 대비)
    text = _read_qr(qd, warped[max(0, y - m):y + bh + m, max(0, x - m):x + bw + m])
    if not text:
        text = _read_qr(qd, photo_bgr)
        if text:
            res.notes.append("QR을 정렬 이미지에서 못 읽어 원본에서 읽음")
    res.qr_text = text or None                                        # 빈 문자열이면 None으로 통일
    res.qr_info = parse_qr_payload(text) if text else None            # "CM1|시험ID|양식ID|버전|과목|시험명" → dict
    return res


def load_template_registry(folder: str) -> dict[tuple[str, str], dict]:
    """폴더의 *.layout.json을 모두 읽어 {(template_id, template_version): layout} 사전으로.
    QR에서 읽은 양식 ID로 좌표를 바로 찾을 때 사용.

    매개변수:
        folder: layout.json 파일들이 있는 폴더 경로 (예: "storage/templates")
    반환:
        dict. 키 = (양식 ID, 양식 버전) 튜플, 값 = layout dict
        예: {("P45Q5C", "1"): {...layout...}}  (P45Q5C = 세로·45문항·5지선다)
        같은 키가 여러 파일에 있으면 나중에 읽은 파일이 덮어씀
    예외:
        json.JSONDecodeError: JSON 형식이 깨진 파일이 있을 때
        KeyError: layout에 template_id / template_version 키가 없을 때
    """
    reg = {}   # (template_id, template_version) → layout
    for p in glob.glob(os.path.join(folder, "*.layout.json")):
        with open(p, encoding="utf-8") as f:
            lay = json.load(f)
        reg[(lay["template_id"], lay["template_version"])] = lay
    return reg


def crop_regions(warped: np.ndarray, layout: dict, dpi: int = F.CANONICAL_DPI, pad_mm: float = 0.6) -> dict:
    """layout 좌표로 칸 이미지 잘라내기 (CNN 학습 데이터·검수 화면·OCR 입력용).
    반환: {"mcq": {문항: [선지별 이미지]}, "sid": {자리: [0~9 이미지]}, "short_answers": {번호: 이미지}}
    동그라미는 pad_mm(0.6mm)만큼 여유를 두고 자름 (정렬 오차로 가장자리가 잘리지 않게).
    주관식은 테두리 선을 뺀 answer_bbox를 여유 없이 자름 (선이 글자로 읽히지 않게).

    매개변수:
        warped: align()이 만든 정면 이미지 (AlignResult.warped)
        layout: 양식 좌표 dict (sheetgen/layout.py가 만든 것, layout.json 내용)
        dpi: warped의 해상도 (align()에 준 값과 같아야 좌표가 맞음, 기본 200)
        pad_mm: 동그라미 칸 주변에 더 붙일 여유 (mm, 기본 0.6)
    반환:
        dict (위 형식). 이미지는 warped의 일부를 자른 numpy 배열(BGR)
        예: out["mcq"][1][2] = 1번 문항 3번째 선지 동그라미 이미지
            out["sid"][0][7] = 학번 첫째 자리의 숫자 7 칸 이미지 (자리별 목록은 layout의 칸 순서를 따름)
    """
    def cut(bbox, pad=pad_mm):
        """mm 사각형 [x, y, w, h]를 사방으로 pad mm 넓혀서 warped에서 잘라냄.

        매개변수:
            bbox: 칸 영역 [x, y, 너비, 높이] (mm, 용지 왼쪽 위 기준)
            pad: 사방에 더할 여유 (mm, 기본은 바깥 함수의 pad_mm)
        반환:
            잘라낸 이미지 (numpy 배열). 이미지 밖으로 나가는 부분은 잘려서 작아질 수 있음
        """
        x, y, w, h = bbox
        X, Y, W, H = bbox_to_px([x - pad, y - pad, w + 2 * pad, h + 2 * pad], dpi)   # 여유 포함 영역 px
        return warped[max(0, Y):Y + H, max(0, X):X + W]                                # 음수 좌표는 0으로 막음

    out = {"mcq": {}, "sid": {}, "short_answers": {}}
    # 객관식: 문항 번호 → 선지 순서대로 동그라미 이미지 목록
    for q in layout["mcq"]["questions"]:
        out["mcq"][q["q"]] = [cut(c["bbox_mm"]) for c in q["choices"]]
    # 학번: 자리 번호(digit_index, 0부터) → 그 자리의 동그라미 이미지 목록
    for b in layout["student_id"]["bubbles"]:
        out["sid"].setdefault(b["digit_index"], []).append(cut(b["bbox_mm"]))
    # 주관식: 문항 번호 → 답 쓰는 칸 이미지 (테두리 선이 안 들어가게 여유 0)
    for it in layout["short_answers"]["items"]:
        out["short_answers"][it["no"]] = cut(it["answer_bbox_mm"], pad=0)
    return out
