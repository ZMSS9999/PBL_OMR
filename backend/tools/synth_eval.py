# pyright: basic
"""합성 데이터셋(synthetic_dataset)으로 파이프라인 테스트 + 정답 라벨과 비교하는 평가 도구.

[언제 쓰나]
  다른 팀원이 만든 합성 답안지 사진 데이터셋(사진 + 정답 라벨 json, 방향별 수천 장)으로
  규칙 기반 판독(P3)의 정확도와 정렬(P1) 오차를 대량으로 측정할 때 사용.
  판독 기준값(0.60 / 0.25)을 바꿀지 판단하는 근거 자료(채움 비율 분포)도 여기서 나옴.

[데이터셋 폴더 구조] (코드가 기대하는 모양)
  <data>/landscape/00001.jpg + 00001.json      ← 이름이 숫자 5자리 = 조건 없는 정면 원본
  <data>/landscape/00001_xxx.jpg + .json        ← 이름에 '_'가 있으면 각도·조명·그림자 조건이 붙은 변형 사진
  <data>/portrait/...                           ← 세로형도 같은 구조
  json에서 쓰는 값: image, orientation, sheet_id, condition(angle, lighting, shadow),
                    homography_template_to_image(템플릿 → 사진 변환 행렬), template_size_px,
                    questions[](question, case, marked_options, option, options[](option, polygon_px, ink))

[두 가지 실행 방식]
  - 기본: 데이터셋 어댑터 (align_synth + 데이터셋 json의 칸 위치). 생성기가 팀 양식과 달랐던 때 만든 방식
  - --pipeline: 실제 파이프라인 그대로 (pipeline/align.py의 align() + 우리 생성기의 45문항·5지선다 layout)
    2026-10-14부터 생성기가 팀 양식 좌표(json양식)와 같아져서 데이터셋 사진을 우리 코드로 바로 읽을 수 있음
    방향(가로/세로)도 align()이 직접 판별하고, 라벨 방향과 다르면 정렬 실패로 셈

[데이터셋 특징]
  - 마커: DICT_6X6_50, ID 0=왼위 1=오위 2=왼아래 3=오아래 (가로·세로 같은 ID, 우리 생성기와 같음)
  - QR 칸이 비어 있음 → P2(시험 확인)는 건너뜀
  - 기본 방식에서는 칸 위치를 데이터셋 json의 polygon_px로 정함 (--pipeline은 우리 layout)
  - 학번 정답 라벨 없음 → 학번 판독은 평가 안 함

[하는 일]
  P1 정렬 : 마커 4개 코너 16점 + RANSAC 호모그래피 → 템플릿 크기로 펴기 (align.py와 같은 방식)
            + 라벨의 실제 호모그래피와 비교해 정렬 오차(px) 측정
  P3 판독 : pipeline/read.py의 read_sheet()를 그대로 사용 (기준값 0.60 / 0.25)
  비교    : 문항마다 라벨 답과 비교 → 정답 / 검수 대기(보류) / 오판독
  P4·P5  : 정면 사진(조건 없는 원본)만 모아 채점 → 엑셀 (가로·세로 각각)

[실행] (backend 폴더에서)
  python tools/synth_eval.py --data <synthetic_dataset 폴더> --out <결과 폴더> [--workers 2] [--limit 0]
  # 한 번에 오래 못 돌리는 환경이면 나눠서 실행 후 합치기
  python tools/synth_eval.py --data ... --out ... --shard 0/8   (… 7/8까지)
  python tools/synth_eval.py --data ... --out ... --merge

[명령행 옵션]
  --data    : 합성 데이터셋 폴더 (필수). 안에 landscape/, portrait/ 폴더가 있어야 함
  --out     : 결과 폴더 (필수). 없으면 만듦
  --workers : 동시에 돌릴 프로세스 수 (기본 2). CPU 코어가 많으면 늘려서 빠르게
  --limit   : 방향별 최대 장 수 (기본 0 = 전부). 예: 50이면 가로 50장 + 세로 50장만 → 빠른 확인용
  --shard   : "i/n" 형식. 전체 작업을 n조각으로 나눠 i번째 조각만 처리하고 part_i.pkl로 저장 (i는 0부터)
  --merge   : 붙이면 사진 처리 없이 <out>/part_*.pkl 조각들을 합쳐서 결과 파일만 작성
  --pipeline: 붙이면 데이터셋 어댑터 대신 실제 파이프라인(align() + 우리 layout)으로 정렬·판독

[결과 확인]
  터미널 출력
    "사진 N장 처리 시작" → 200장마다 "  처리한 수/전체  경과 초" 진행 표시
    마지막에 요약 JSON 한 줄
      images        : 전체 사진 수
      aligned       : 정렬 성공 수
      align_err_px  : 정렬 오차(px) 평균 / 상위 5% 경계값(p95) / 최대
      overall       : 문항 판정 개수와 비율. correct(정답) / review(검수 대기) / wrong(오판독, 0에 가까워야 함)
    "완료: <결과 폴더>"
  생성 파일 (<out> 폴더)
    images.csv     : 사진 한 장당 한 줄 (조건, 정렬 성공, 마커, 정렬 오차, 처리 시간, 판정 개수)
    questions.csv  : 문항 하나당 한 줄 (유형, 기대 답, 읽은 답, 상태, 판정, 선지별 채움 비율, 잉크 종류)
    summary.json   : 전체 요약 + 유형·방향·각도·조명·그림자별 표 + 오판독 내용 + 채움 비율 분포
    채점결과_가로형_정면원본.xlsx, 채점결과_세로형_정면원본.xlsx : 정면 원본만 P4 채점 → P5 엑셀
    progress.txt   : 진행 상황 (다른 창에서 확인용)
    part_<i>.pkl   : --shard로 실행했을 때의 중간 결과 조각

[내부 처리 순서]
  1. 방향마다 build_profile()로 데이터셋 양식 정보(칸 위치, 마커 위치) 만들기
  2. 사진마다 process() (여러 프로세스로 동시에): P1 정렬(align_synth) → P3 판독 → 라벨과 비교
  3. finish(): csv 저장 → summarize()로 집계 → export_excel()로 엑셀 → summary.json

[다른 파일과의 관계]
  - pipeline/read.py         : P3 판독 함수 read_sheet()와 기준값 ReadConfig를 그대로 사용
  - pipeline/flow.py         : P4 step_grade(), P5 step_export() 사용
  - grading/answer_key.py    : 정답표 자료형 AnswerKey
  - pipeline/align.py         : --pipeline일 때 P1 정렬 align() 사용 (기본 방식은 align_synth가 대신함)
  - sheetgen/layout.py       : --pipeline일 때 compute_layout()으로 45문항·5지선다 layout 계산
"""
import argparse
import csv
import glob
import json
import os
import pickle
import sys
import time
from collections import Counter, defaultdict
from multiprocessing import Pool

import cv2
import numpy as np

# backend 폴더를 import 경로에 추가 → backend 안의 grading, pipeline 패키지를 불러올 수 있게 함
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from grading.answer_key import AnswerKey  # noqa: E402
from pipeline import flow  # noqa: E402
from pipeline.align import AlignError, align  # noqa: E402
from pipeline.read import ReadConfig, read_sheet  # noqa: E402
from sheetgen import frame as F  # noqa: E402
from sheetgen.layout import SheetSpec, compute_layout  # noqa: E402

DICT = cv2.aruco.DICT_6X6_50   # 데이터셋 마커의 ArUco 사전 종류 (6x6 칸 무늬, ID 0~49)
MARKER_IDS = (0, 1, 2, 3)      # 정렬에 쓰는 마커 ID (0=왼위 1=오위 2=왼아래 3=오아래)
DPI = 192                      # 템플릿 2246px / A4 297mm ≈ 192dpi. mm 변환용 (read.py가 mm 단위 좌표를 받음)
PX2MM = 25.4 / DPI             # 템플릿 px 1개 = 몇 mm인지 (약 0.132mm)
# 데이터셋 마킹 유형(case) 이름 → 한글 설명 (보고서를 읽을 때 참고용 표)
CASE_KO = {"standard": "정상 마킹", "blank": "빈칸", "thin": "얇게(일부만) 채움", "check": "체크 표시",
           "circle": "동그라미 표시", "erased": "지운 흔적+새 마킹", "faint": "흐릿한 마킹",
           "double": "이중 마킹", "overflow": "칸 삐져나옴"}


# ---------------- 데이터셋 양식 정보(프로필) 만들기 ----------------
def _detector():
    """데이터셋 마커(DICT_6X6_50)용 ArUco 검출기 만들기.

    서브픽셀 코너 보정을 켜서 마커 코너를 소수점 px 단위로 찾음 (정렬 정확도 향상).

    매개변수:
        없음
    반환:
        cv2.aruco.ArucoDetector 객체 (detectMarkers(이미지)로 마커 검출)
    """
    p = cv2.aruco.DetectorParameters()
    p.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    return cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(DICT), p)


def _to_tmpl(pts, H_t2i):
    """사진 좌표 → 템플릿 좌표 (라벨 호모그래피의 역행렬 사용).

    라벨에는 '템플릿 → 사진' 행렬만 있으므로, 역행렬을 곱해 사진 위의 점을 템플릿(정면 원본) 위의 점으로 되돌림.

    매개변수:
        pts: 사진 위 점 목록 [[x, y], ...] (px)
        H_t2i: 템플릿 → 사진 변환 행렬 (3x3, 라벨의 homography_template_to_image)
    반환:
        템플릿 좌표 배열 (N x 2, px)
    """
    p = np.c_[np.asarray(pts, float), np.ones(len(pts))] @ np.linalg.inv(H_t2i).T
    return p[:, :2] / p[:, 2:]


def build_profile(data_dir, orientation, n_calib=5):
    """방향 하나의 양식 정보: 템플릿 크기, 칸 위치(layout), 마커 코너의 템플릿 좌표.
    - 칸 위치: 원본 json의 polygon_px를 템플릿 좌표로 되돌려 중심·반지름 계산 (모든 장이 같은 양식임을 확인함)
    - 마커 코너: 정면 원본 사진 몇 장에서 검출한 코너를 템플릿 좌표로 되돌려 중앙값 사용
      (데이터셋에 마커 좌표가 없어서 이렇게 한 번 측정해 둠)

    매개변수:
        data_dir: 데이터셋 폴더 (안에 landscape/, portrait/)
        orientation: "landscape" 또는 "portrait"
        n_calib: 마커 코너 측정에 쓸 정면 원본 사진 수 (기본 5)
    반환:
        프로필 dict
          "orientation": 방향
          "size": 템플릿 크기 (너비, 높이) px
          "layout": read_sheet()에 넣을 수 있는 우리 양식 모양의 dict (객관식 칸만, 학번 칸 없음)
          "marker_tmpl": {마커 ID: 코너 4점의 템플릿 좌표 [[x, y] x 4]}
    예외:
        IndexError: 정면 원본(숫자 5자리 이름) json이 하나도 없을 때
    """
    # fronts: 정면 원본 라벨 json 목록 (이름이 숫자 5자리). 첫 장으로 칸 위치를 계산
    fronts = sorted(glob.glob(os.path.join(data_dir, orientation, "[0-9][0-9][0-9][0-9][0-9].json")))
    d = json.load(open(fronts[0], encoding="utf-8"))
    H = np.array(d["homography_template_to_image"])
    tw, th = d["template_size_px"]   # 템플릿 너비, 높이 (px)
    # 선지마다 polygon_px(사진 위 칸 테두리)를 템플릿 좌표로 되돌려 중심·반지름 계산 → mm로 바꿔 저장
    # 반지름 = 테두리 가로·세로 폭의 평균 ÷ 2
    questions = []
    for q in d["questions"]:
        choices = []
        for op in q["options"]:
            poly = _to_tmpl(op["polygon_px"], H)
            c = poly.mean(0)
            r = float((poly.max(0) - poly.min(0)).mean() / 2)
            choices.append({"choice": op["option"], "center_mm": [float(c[0] * PX2MM), float(c[1] * PX2MM)],
                            "radius_mm": r * PX2MM})
        questions.append({"q": q["question"], "choices": choices})
    # 정면 원본 n_calib장에서 마커 코너를 검출 → 템플릿 좌표로 되돌려 마커 ID별로 모음 → 중앙값
    corners = defaultdict(list)
    det = _detector()
    for jf in fronts[:n_calib]:
        dj = json.load(open(jf, encoding="utf-8"))
        img = cv2.imread(jf[:-5] + ".jpg", cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        cs, ids, _ = det.detectMarkers(img)
        for c, i in zip(cs, ids.flatten() if ids is not None else []):
            if int(i) in MARKER_IDS:
                corners[int(i)].append(_to_tmpl(c.reshape(4, 2), np.array(dj["homography_template_to_image"])))
    marker_tmpl = {i: np.median(np.array(v), axis=0) for i, v in corners.items()}
    layout = {"template_id": f"synthetic-{orientation}", "template_version": "dataset",
              "mcq": {"questions": questions}, "student_id": {"digits": 0, "bubbles": []}}
    return {"orientation": orientation, "size": (int(tw), int(th)), "layout": layout,
            "marker_tmpl": {k: v.tolist() for k, v in marker_tmpl.items()}}


# ---------------- 사진 한 장 처리 ----------------
def align_synth(img, prof):
    """P1 (데이터셋 마커용): 코너 짝짓기 → RANSAC 호모그래피 → 템플릿 크기로 펴기.

    사진에서 찾은 마커 코너(src)와 프로필의 템플릿 코너(dst)를 짝지어 변환 행렬을 구함.
    마커가 3개 이상 보여야 정렬함 (pipeline/align.py와 같은 기준). 바깥 빈 곳은 흰색으로 채움.

    매개변수:
        img: 사진 (BGR 배열)
        prof: build_profile()이 만든 프로필 dict ("marker_tmpl", "size" 사용)
    반환:
        (warped, H, found)
          warped: 템플릿 크기로 편 정면 이미지 (BGR 배열). 실패 시 None
          H: 사진 → 템플릿 변환 행렬 (3x3). 실패 시 None
          found: 찾은 마커 ID 목록 (정렬됨, 예: [0, 1, 3])
    """
    cs, ids, _ = _detector().detectMarkers(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
    src, dst, found = [], [], []
    for c, i in zip(cs, ids.flatten() if ids is not None else []):
        i = int(i)
        if i in MARKER_IDS and i not in found and i in prof["marker_tmpl"]:
            found.append(i)
            src.extend(c.reshape(4, 2))
            dst.extend(prof["marker_tmpl"][i])
    if len(found) < 3:
        return None, None, sorted(found)
    H, _ = cv2.findHomography(np.asarray(src, np.float32), np.asarray(dst, np.float32), cv2.RANSAC, 3.0)
    if H is None:
        return None, None, sorted(found)
    warped = cv2.warpPerspective(img, H, prof["size"], flags=cv2.INTER_LINEAR,
                                 borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
    return warped, H, sorted(found)


def expected_answer(q):
    """라벨 → 기대 답. 빈칸 [] / 이중 마킹 [a, b] / 나머지 [정답 선지].

    매개변수:
        q: 라벨 json의 문항 하나 dict ("case", "marked_options" 사용)
    반환:
        판독기가 읽어야 하는 답 목록 (예: [3], [2, 4], [])
    """
    return sorted(q["marked_options"]) if q["case"] != "blank" else []


def process(args):
    """사진 1장: P1 정렬 → P3 판독 → 라벨과 비교. 결과는 숫자·문자열만 (프로세스 간 전달용).

    여러 프로세스(Pool)가 동시에 부르는 함수라 인자를 묶음 하나로 받음.

    매개변수:
        args: (jpath, prof) 묶음
              jpath: 라벨 json 경로 (같은 이름의 .jpg가 사진. 예: .../portrait/00001.json)
              prof: build_profile()이 만든 해당 방향 프로필
                    (--pipeline이면 {"orientation", "pipeline": True, "layout": 우리 45문항·5지선다 layout})
    반환:
        사진 결과 dict
          image, orientation, sheet, angle, lighting, shadow : 사진 정보와 촬영 조건
          aligned       : 정렬 성공 여부
          markers       : 찾은 마커 ID 목록
          align_err_px  : 칸 중심 정렬 오차 평균 (px). 실패 시 None
          align_err_max_px : 정렬 오차 최대 (px). 성공했을 때만 있음
          ms            : 정렬+판독 시간 (밀리초). 실패 시 None
          rows          : 문항별 비교 결과 목록 (q, case, expected, pred, status, outcome, fill, ink)
                          outcome = "correct"(정답) / "review"(판정 보류) / "wrong"(오판독)
          read          : 판독 결과 전체 (정면 원본일 때만, 나머지는 None) → P4·P5용
          key           : {문항: 라벨의 정답 선지} → 정답표 만들 때 사용 (성공했을 때만 있음)
    """
    jpath, prof = args
    # d: 라벨 json 내용, img: 사진 (없거나 깨졌으면 None)
    d = json.load(open(jpath, encoding="utf-8"))
    img = cv2.imread(jpath[:-5] + ".jpg")
    t0 = time.perf_counter()
    cond = d["condition"]
    base = {"image": d["image"], "orientation": d["orientation"], "sheet": d["sheet_id"],
            "angle": cond["angle"], "lighting": cond["lighting"], "shadow": cond["shadow"]}
    if img is None:
        return {**base, "aligned": False, "markers": [], "align_err_px": None, "ms": None, "rows": []}
    if prof.get("pipeline"):
        # --pipeline: 실제 P1 정렬 (200dpi 정면 이미지, 방향도 직접 판별)
        try:
            res = align(img)
        except AlignError:
            return {**base, "aligned": False, "markers": [], "align_err_px": None, "ms": None, "rows": []}
        if res.orientation != d["orientation"]:     # 방향을 잘못 판별하면 칸 위치가 전부 틀리므로 실패로 셈
            return {**base, "aligned": False, "markers": res.found_ids, "align_err_px": None, "ms": None,
                    "rows": [], "note": f"방향 오판별 {res.orientation}"}
        warped, found = res.warped, res.found_ids
        k = F.CANONICAL_DPI / DPI                    # 템플릿 px(192dpi) → 정렬 이미지 px(200dpi) 배율
        H = np.diag([1 / k, 1 / k, 1.0]) @ res.H     # 사진 → 템플릿 px (오차 계산을 기본 방식과 같은 단위로)
        read = read_sheet(warped, prof["layout"])    # 기준값·해상도(200dpi) 모두 기본값
    else:
        warped, H, found = align_synth(img, prof)
        if warped is None or H is None:
            return {**base, "aligned": False, "markers": found, "align_err_px": None, "ms": None, "rows": []}
        # P3 판독: 기준값은 기본값 그대로, 해상도만 데이터셋 템플릿에 맞춰 192dpi
        read = read_sheet(warped, prof["layout"], ReadConfig(dpi=DPI))
    ms = (time.perf_counter() - t0) * 1000
    # 정렬 오차: 칸 중심을 (우리 H의 역) vs (라벨 H)로 사진에 찍었을 때 거리
    centers = np.array([[c["center_mm"][0] / PX2MM, c["center_mm"][1] / PX2MM]
                        for q in prof["layout"]["mcq"]["questions"] for c in q["choices"]])
    gt = cv2.perspectiveTransform(centers.reshape(-1, 1, 2), np.array(d["homography_template_to_image"])).reshape(-1, 2)
    est = cv2.perspectiveTransform(centers.reshape(-1, 1, 2), np.linalg.inv(H)).reshape(-1, 2)
    err = np.linalg.norm(gt - est, axis=1)   # 칸마다 정렬 오차 (사진 px)
    # 문항마다 라벨 기대 답과 읽은 답 비교: 답 None → review, 같음 → correct, 다름 → wrong
    # ink: 선지별 잉크 종류 (채움 비율 분포를 잉크별로 보기 위함)
    rows = []
    by_q = {r["q"]: r for r in read["mcq"]}
    for q in d["questions"]:
        r = by_q[q["question"]]
        exp = expected_answer(q)
        pred = r["answer"]
        outcome = "review" if pred is None else ("correct" if sorted(pred) == exp else "wrong")
        inks = {op["option"]: op["ink"] for op in q["options"]}
        rows.append({"q": q["question"], "case": q["case"], "expected": exp, "pred": pred, "status": r["status"],
                     "outcome": outcome, "fill": r["fill"], "ink": [inks[k] for k in sorted(inks)]})
    is_original = "_" not in os.path.basename(jpath)      # 조건 없는 정면 원본 → P4·P5용으로 판독 결과 보관
    return {**base, "aligned": True, "markers": found, "align_err_px": float(err.mean()),
            "align_err_max_px": float(err.max()), "ms": ms, "rows": rows, "read": read if is_original else None,
            "key": {q["question"]: q["option"] for q in d["questions"]}}


# ---------------- 집계 ----------------
def pct(n, d):
    """비율을 백분율(%)로, 소수 둘째 자리까지.

    매개변수:
        n: 해당 개수 (예: 45)
        d: 전체 개수 (예: 50)
    반환:
        백분율 실수 (예: 90.0). 전체가 0이면 0.0
    """
    return round(100 * n / d, 2) if d else 0.0


def summarize(results):
    """사진별 결과 목록 → 전체 요약 dict (summary.json 내용).

    정렬 성공률·오차, 문항 판정 비율을 전체와 조건별(유형·방향·각도·조명·그림자)로 집계하고,
    오판독 내용과 칸 채움 비율 분포(판독 기준값 조정 근거)를 정리함.

    매개변수:
        results: process()가 돌려준 dict 목록
    반환:
        요약 dict
          images, aligned            : 전체 사진 수, 정렬 성공 수
          align_err_px               : 정렬 오차 {"mean", "p95", "max"} (px)
          ms_mean                    : 사진 한 장 평균 처리 시간 (밀리초)
          questions                  : 비교한 문항 수
          overall                    : correct / review / wrong 개수와 비율(%)
          by_case, by_orientation, by_angle, by_lighting, by_shadow : 조건별 {"n", "correct_%", "review_%", "wrong_%"}
          wrong_detail               : 오판독 내용 {"유형->읽은 답 종류": 개수} (예: {"faint->blank": 3})
          fill_pct                   : {"유형/잉크": 채움 비율 백분위수(1, 5, 25, 50, 75, 95, 99) + n}
    """
    out = {}
    imgs = len(results)
    aligned = [r for r in results if r["aligned"]]
    out["images"] = imgs
    out["aligned"] = len(aligned)
    # 정렬 오차 통계 (성공한 사진이 없으면 0 하나로 계산해 오류 방지)
    errs = np.array([r["align_err_px"] for r in aligned]) if aligned else np.array([0.0])
    out["align_err_px"] = {"mean": round(float(errs.mean()), 3), "p95": round(float(np.percentile(errs, 95)), 3),
                           "max": round(float(errs.max()), 3)}
    out["ms_mean"] = round(float(np.mean([r["ms"] for r in aligned])), 1) if aligned else None
    rows = [(r, x) for r in aligned for x in r["rows"]]   # (사진 결과, 문항 결과) 쌍 전체
    tot = Counter(x["outcome"] for _, x in rows)
    n = len(rows)
    out["questions"] = n
    out["overall"] = {k: tot[k] for k in ("correct", "review", "wrong")} | {
        "correct_%": pct(tot["correct"], n), "review_%": pct(tot["review"], n), "wrong_%": pct(tot["wrong"], n)}

    def table(keyf):
        """문항 판정을 keyf가 고른 기준(조건)별로 묶어 비율 표 만들기 (summarize 안에서만 쓰는 보조 함수).

        매개변수:
            keyf: (사진 결과, 문항 결과) → 묶을 기준 값을 돌려주는 함수 (예: lambda r, x: x["case"])
        반환:
            {기준 값: {"n": 문항 수, "correct_%", "review_%", "wrong_%"}} (기준 값 순으로 정렬)
        """
        t = defaultdict(Counter)
        for r, x in rows:
            t[keyf(r, x)][x["outcome"]] += 1
        return {k: {"n": sum(c.values()), "correct_%": pct(c["correct"], sum(c.values())),
                    "review_%": pct(c["review"], sum(c.values())), "wrong_%": pct(c["wrong"], sum(c.values()))}
                for k, c in sorted(t.items())}
    out["by_case"] = table(lambda r, x: x["case"])
    out["by_orientation"] = table(lambda r, x: r["orientation"])
    out["by_angle"] = table(lambda r, x: r["angle"])
    out["by_lighting"] = table(lambda r, x: r["lighting"])
    out["by_shadow"] = table(lambda r, x: r["shadow"])
    # 오판독 내용: (유형, 기대 → 읽은 답 상태)
    wrong = Counter((x["case"], "multi" if x["pred"] and len(x["pred"]) > 1 else ("blank" if x["pred"] == [] else "other"))
                    for _, x in rows if x["outcome"] == "wrong")
    out["wrong_detail"] = {f"{c}->{k}": v for (c, k), v in wrong.most_common()}
    # 칸 채움 비율 분포 (잉크 종류·유형별) → 기준값 조정 근거
    fills = defaultdict(list)
    for _, x in rows:
        for f, ink in zip(x["fill"], x["ink"]):
            fills[f"{x['case']}/{ink}"].append(f)
    out["fill_pct"] = {k: {p: round(float(np.percentile(v, p)), 3) for p in (1, 5, 25, 50, 75, 95, 99)}
                       | {"n": len(v)} for k, v in sorted(fills.items())}
    return out


def write_csv(results, out_dir):
    """사진별·문항별 결과를 csv 파일 2개로 저장 (엑셀에서 열어 정렬·필터로 분석하기 좋게).

    인코딩 utf-8-sig = 엑셀에서 열어도 한글이 깨지지 않는 형식.
    images.csv    : 사진 한 장당 한 줄 (조건, 정렬 결과, 판정 개수)
    questions.csv : 문항 하나당 한 줄 (기대 답, 읽은 답('?' = 판정 보류), 선지별 채움 비율 fill1~5, 잉크)

    매개변수:
        results: process()가 돌려준 dict 목록
        out_dir: 저장할 폴더
    반환:
        None
    """
    with open(os.path.join(out_dir, "images.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["image", "orientation", "sheet", "angle", "lighting", "shadow", "aligned", "markers",
                    "align_err_px", "ms", "correct", "review", "wrong"])
        for r in results:
            c = Counter(x["outcome"] for x in r["rows"])
            w.writerow([r["image"], r["orientation"], r["sheet"], r["angle"], r["lighting"], r["shadow"], r["aligned"],
                        " ".join(map(str, r["markers"])), r["align_err_px"] and round(r["align_err_px"], 3),
                        r["ms"] and round(r["ms"]), c["correct"], c["review"], c["wrong"]])
    with open(os.path.join(out_dir, "questions.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["image", "orientation", "angle", "lighting", "shadow", "q", "case", "expected", "pred", "status",
                    "outcome", "fill1", "fill2", "fill3", "fill4", "fill5", "ink"])
        for r in results:
            for x in r["rows"]:
                w.writerow([r["image"], r["orientation"], r["angle"], r["lighting"], r["shadow"], x["q"], x["case"],
                            ",".join(map(str, x["expected"])), "?" if x["pred"] is None else ",".join(map(str, x["pred"])),
                            x["status"], x["outcome"], *x["fill"], "/".join(x["ink"])])


def export_excel(results, out_dir):
    """P4·P5: 정면 원본 사진만 모아 채점 → 엑셀. 정답표는 라벨의 정답 선지로 만듦 (빈칸 문항은 1번으로 둠).

    방향(가로·세로)마다 엑셀 하나. 정답표는 첫 번째 사진의 라벨로 45문항 5지선다를 가정해 만듦.

    매개변수:
        results: process()가 돌려준 dict 목록
        out_dir: 저장할 폴더
    반환:
        만들어진 엑셀 경로 목록 (예: [".../채점결과_가로형_정면원본.xlsx", ".../채점결과_세로형_정면원본.xlsx"])
        해당 방향의 정면 원본이 없으면 그 방향은 빠짐
    """
    paths = []
    for o, name in (("landscape", "가로"), ("portrait", "세로")):
        # rs: 이 방향에서 정렬에 성공한 정면 원본(이름에 '_' 없음) 결과
        rs = [r for r in results if r["orientation"] == o and r["aligned"] and r["image"].count("_") == 0]
        if not rs:
            continue
        key = AnswerKey(num_questions=45, num_choices=5,
                        answers={int(q): [v if v else 1] for q, v in rs[0]["key"].items()}, exam_id=f"synthetic-{o}")
        grades = []
        for r in sorted(rs, key=lambda r: r["sheet"]):
            g = flow.step_grade(r["read"], key)
            g["source"] = r["image"]
            grades.append(g)
        paths.append(flow.step_export(grades, key, {"exam_name": f"합성 데이터 {name}형", "subject": "테스트"},
                                      os.path.join(out_dir, f"채점결과_{name}형_정면원본.xlsx")))
    return paths


def main():
    """명령행 옵션을 읽어 평가 전체를 실행 (또는 --merge로 조각 합치기, --shard로 조각만 처리).

    매개변수:
        없음 (명령행 옵션을 sys.argv에서 읽음)
    반환:
        None. 결과는 터미널 출력과 --out 폴더의 파일로 나옴
    예외:
        IndexError: 데이터셋 폴더에 정면 원본 json이 없을 때 (build_profile)
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="synthetic_dataset 폴더 (landscape/, portrait/ 포함)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--limit", type=int, default=0, help="방향별 최대 장 수 (0 = 전부)")
    ap.add_argument("--shard", default="", help="i/n: 전체의 n분의 1만 처리하고 part_i.pkl로 저장")
    ap.add_argument("--merge", action="store_true", help="part_*.pkl을 합쳐서 결과 파일 작성")
    ap.add_argument("--pipeline", action="store_true", help="실제 파이프라인(align + 우리 layout)으로 평가")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    # --merge: 조각 파일(part_*.pkl)만 읽어서 합친 뒤 결과 파일 작성
    if a.merge:
        results = []
        for f in sorted(glob.glob(os.path.join(a.out, "part_*.pkl"))):
            with open(f, "rb") as fh:
                results += pickle.load(fh)
        finish(results, a.out)
        return

    # jobs: (라벨 json 경로, 방향 프로필) 목록. 같은 이름의 .jpg가 있는 json만 포함
    jobs = []
    for o in ("landscape", "portrait"):
        if a.pipeline:   # 실제 파이프라인: 우리 생성기의 45문항·5지선다 layout (팀 양식 좌표와 같음)
            prof = {"orientation": o, "pipeline": True,
                    "layout": compute_layout(SheetSpec(num_questions=45, num_choices=5, orientation=o))}
        else:
            prof = build_profile(a.data, o)
        files = sorted(glob.glob(os.path.join(a.data, o, "*.json")))
        files = [f for f in files if os.path.exists(f[:-5] + ".jpg")]
        if a.limit:
            files = files[:a.limit]
        jobs += [(f, prof) for f in files]
    if a.shard:
        i, n = map(int, a.shard.split("/"))
        jobs = jobs[i::n]   # i번째부터 n개 간격으로 골라 전체의 1/n만 처리
    print(f"사진 {len(jobs)}장 처리 시작", flush=True)
    # 여러 프로세스로 process()를 동시에 실행 (끝나는 순서대로 받음). 200장마다 진행 상황 출력·기록
    results, t0 = [], time.time()
    with Pool(a.workers) as pool:
        for i, r in enumerate(pool.imap_unordered(process, jobs, chunksize=4), 1):
            results.append(r)
            if i % 200 == 0 or i == len(jobs):
                print(f"  {i}/{len(jobs)}  {time.time() - t0:.0f}s", flush=True)
                with open(os.path.join(a.out, "progress.txt"), "w") as f:
                    f.write(f"{i}/{len(jobs)} {time.time() - t0:.0f}s\n")
    # --shard면 결과 조각만 저장하고 끝 (나중에 --merge로 합침)
    if a.shard:
        with open(os.path.join(a.out, f"part_{a.shard.split('/')[0]}.pkl"), "wb") as fh:
            pickle.dump(results, fh)
        print("조각 저장 완료", flush=True)
        return
    finish(results, a.out)


def finish(results, out):
    """결과 목록 → csv, summary.json, 엑셀.

    결과를 (방향, 이미지 이름) 순으로 정렬한 뒤 저장하고, 터미널에 요약 JSON 한 줄을 출력함.

    매개변수:
        results: process()가 돌려준 dict 목록 (이 함수 안에서 정렬되어 순서가 바뀜)
        out: 결과 폴더 경로
    반환:
        None
    """
    a = argparse.Namespace(out=out)
    results.sort(key=lambda r: (r["orientation"], r["image"]))
    write_csv(results, a.out)
    s = summarize(results)
    s["excel"] = export_excel(results, a.out)
    with open(os.path.join(a.out, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: s[k] for k in ("images", "aligned", "align_err_px", "overall")}, ensure_ascii=False))
    print("완료:", a.out)


if __name__ == "__main__":
    main()
