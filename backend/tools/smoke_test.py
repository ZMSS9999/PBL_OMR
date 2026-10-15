# pyright: basic
"""인쇄·촬영 없이 좌표 JSON이 맞는지 확인하는 스모크 테스트.

  PDF → 이미지(200dpi) → 가짜 마킹 → 가짜 '스마트폰 사진'(원근·회전·조명·흐림·노이즈·JPEG)
      → ArUco 정렬 → QR로 양식 찾기 → 저장 좌표로 칸 추출 → 규칙 기반 판독(pipeline/read.py)

[하는 일]
  실제 프린터·스마트폰 없이, 컴퓨터 안에서 "찍은 사진"을 흉내 내어 P1 정렬이 잘 되는지,
  저장된 좌표(layout.json)가 실제 칸 위치와 맞는지, P3 판독이 답을 제대로 읽는지 확인함.
  정렬·좌표 코드를 고친 뒤 망가진 곳이 없는지 빠르게 점검(스모크 테스트)하는 용도.

측정: 마커 검출 수, QR 디코드 성공, 정렬 오차(칸 중심이 원래 위치에서 몇 px/mm 벗어났는지),
      판독 정확도(학생이 표시한 답을 제대로 읽었는지). 마킹 유형별 비교는 tools/demo_end_to_end.py.

사용:
  (backend 폴더에서) python tools/smoke_test.py --num-questions 45 --num-choices 5 --orientation portrait --trials 10

[명령행 옵션] (모두 선택, 괄호 안은 기본값)
  --num-questions : 객관식 문항 수 (45, 범위 5~45)
  --num-choices   : 선지 수 (5, 범위 2~5)
  --orientation   : 용지 방향 portrait(세로) / landscape(가로) (portrait)
  --trials        : 난이도별 반복 횟수 = 난이도마다 만들 가상 사진 수 (10)
  --seed          : 난수 시드 (0). 같은 값이면 매번 같은 결과 (재현용)
  --out           : 결과 이미지·보고서 폴더 (smoke_out)
  --levels        : 시험할 촬영 난이도, 쉼표로 구분 (easy,medium,hard). 내용은 LEVELS 참고

[결과 확인]
  터미널 표 (난이도마다 한 줄)
    level          : 촬영 난이도 (easy / medium / hard)
    정렬성공       : 정렬 성공 수 / 시도 수 (예: 10/10)
    마커4개        : 마커 4개를 모두 찾은 사진 수 (3개만 찾아도 정렬은 됨)
    QR             : QR로 양식을 찾았고 방향(세로/가로)도 맞은 사진 수
    중심오차평균mm : 칸 중심이 원래 위치에서 벗어난 거리의 평균 (mm). 작을수록 좋음 (보통 0.1mm 이하)
    최대mm         : 위 거리의 최댓값 (mm)
    객관식판독     : 객관식 판독 정확도 평균 (0~1, 1.000 = 모든 문항을 라벨대로 읽음)
    학번           : 학번을 정확히 읽은 사진 수
    ms             : 정렬(P1) 한 번에 걸린 평균 시간 (밀리초)
  생성 파일 (<out> 폴더)
    report.json                      ← 사진 한 장마다의 측정값 전체
    <난이도>_1_photo.jpg              ← 가상 촬영 사진 (25% 축소)
    <난이도>_2_aligned_overlay.jpg    ← 정렬된 이미지 위에 칸 위치를 그린 것 (50% 축소)
                                        초록 굵은 원 = 판독된 답, 파란 원 = 나머지 칸,
                                        빨간 사각형 = 주관식 답 칸, 보라 사각형 = QR 위치
                                        → 원이 실제 동그라미와 겹치면 좌표·정렬이 맞는 것
    <난이도>_3_mcq_crops.png          ← 잘라낸 객관식 칸 모음 (앞 20문항, 한 줄 = 한 문항)
    <난이도>_4_short_answer_<번호>.jpg ← 잘라낸 주관식 답 칸
    FAIL_<난이도>_<회차>.jpg          ← 정렬에 실패한 사진 (25% 축소)
  ※ 실행할 때마다 storage/에 '스모크 테스트' 시험이 하나씩 새로 생김 (웹과 같은 함수로 만들기 때문)

[내부 처리 순서]
  1. 답안지 생성(service.create_answer_sheet) → PDF를 200dpi 이미지로 변환 (pdf_to_bgr)
  2. 난이도마다 trials번 반복
     가짜 마킹(draw_marks) → 가짜 사진(simulate_photo) → P1 정렬(align)
     → 칸 중심 정렬 오차 계산 → P3 판독(read_sheet) → 라벨과 비교
     → 첫 회차만 예시 이미지 저장 (overlay, crop_montage)
  3. report.json 저장, 난이도별 요약 표 출력

[다른 파일과의 관계]
  - pipeline/align.py, pipeline/read.py : 시험 대상 (P1 정렬, P3 판독)
  - app/service.py, sheetgen/layout.py  : 답안지 생성, 좌표 변환 함수
  - tools/demo_end_to_end.py            : 이 파일의 LEVELS, pdf_to_bgr, simulate_photo를 가져다 씀
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import cv2
import numpy as np

# backend 폴더를 import 경로에 추가 → backend 안의 pipeline, app, sheetgen 패키지를 불러올 수 있게 함
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pipeline.align import AlignError, align, crop_regions  # noqa: E402
from pipeline.read import read_sheet  # noqa: E402
from app import service  # noqa: E402
from sheetgen import frame as F  # noqa: E402
from sheetgen.layout import all_bubbles, bbox_to_px, mm_to_px  # noqa: E402

DPI = F.CANONICAL_DPI   # 기준 이미지 해상도 (200dpi). A4 세로 = 1654 x 2339 px
# 촬영 난이도별 사진 왜곡 정도. 각 값의 뜻
#   jitter : 용지 네 모서리를 무작위로 흔드는 정도 (용지 크기 대비 비율. 0.08 = 8%) → 원근(비스듬히 찍음)
#   rot    : 최대 회전 각도 (도)
#   light  : 가장 어두운 쪽의 밝기 배율 (1.0 = 균일, 0.5 = 한쪽이 절반 밝기)
#   blur   : 가우시안 흐림 σ (px, 클수록 초점이 안 맞은 사진)
#   noise  : 픽셀 노이즈 σ (밝기 0~255 단위)
#   jpeg   : JPEG 저장 품질 (0~100, 낮을수록 압축 손상 큼)
#   shadow : True면 손·폰 그림자 같은 어두운 얼룩 추가
LEVELS = {  # 코너 흔들림(용지 대비), 회전(도), 최저 조명 배율, 흐림 σ, 노이즈 σ, JPEG 품질
    "easy":   dict(jitter=0.01, rot=2,  light=0.90, blur=0.6, noise=2, jpeg=92, shadow=False),
    "medium": dict(jitter=0.04, rot=8,  light=0.70, blur=1.2, noise=4, jpeg=80, shadow=False),
    "hard":   dict(jitter=0.08, rot=15, light=0.50, blur=2.0, noise=7, jpeg=65, shadow=True),
}


def pdf_to_bgr(pdf_path: str, dpi: int = DPI) -> np.ndarray:
    """PDF 첫 페이지 → 이미지. pdf2image(poppler)가 없으면 pypdfium2로 시도.

    인쇄한 종이를 대신해 PDF를 그대로 그림으로 바꿔 '완벽한 인쇄본'으로 사용함.

    매개변수:
        pdf_path: 답안지 PDF 경로 (예: storage/exams/EX-.../answer_sheet.pdf)
        dpi: 변환 해상도 (기본 200 → A4 세로 1654 x 2339 px)
    반환:
        BGR 이미지 배열 (OpenCV 형식, 높이 x 너비 x 3)
    예외:
        SystemExit: pdf2image가 실패했고 pypdfium2도 설치돼 있지 않을 때 (설치 안내 메시지와 함께 종료)
    """
    try:
        from pdf2image import convert_from_path
        return cv2.cvtColor(np.array(convert_from_path(pdf_path, dpi=dpi)[0]), cv2.COLOR_RGB2BGR)
    except Exception as e1:  # noqa: BLE001
        try:
            import pypdfium2 as pdfium  # type: ignore[import-not-found]
            pil = pdfium.PdfDocument(pdf_path)[0].render(scale=dpi / 72).to_pil()  # pyright: ignore[reportArgumentType]  (float 배율도 동작함)
            return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
        except ImportError:
            raise SystemExit(f"PDF를 이미지로 바꿀 수 없습니다 ({e1}). "
                             "poppler를 설치하거나 'pip install pypdfium2'를 실행하세요")


# ---------- 가짜 마킹 ----------
def draw_marks(page: np.ndarray, layout: dict, rng) -> tuple[np.ndarray, dict]:
    """빈 답안지 이미지에 가짜 학생 마킹(객관식·학번)과 가짜 주관식 글씨를 그림.

    객관식은 문항마다 5% 확률로 빈칸, 나머지는 무작위 선지 하나를 진하게 칠함.
    학번은 자리마다 0~9 중 하나를 칠함. 주관식 칸에는 영어 단어 3개를 필기체 글꼴로 적음.
    (마킹 유형을 다양하게 섞은 시험은 demo_end_to_end.py)

    매개변수:
        page: 빈 답안지 이미지 (BGR 배열, 200dpi). 원본은 바뀌지 않음 (복사해서 그림)
        layout: 양식 좌표 dict (layout.json 내용)
        rng: 난수 생성기 (np.random.default_rng)
    반환:
        (img, truth)
          img: 마킹된 이미지 (BGR 배열)
          truth: 라벨(학생이 표시한 답) dict
                 {"mcq": {문항: 선지 번호 또는 None(빈칸)}, "sid": [학번 숫자 목록]}
                 (예: {"mcq": {1: 3, 2: None}, "sid": [2, 0, 2, 6, ...]})
    """
    img = page.copy()
    px = lambda v: mm_to_px(v, DPI)  # noqa: E731
    # px(v): mm 값 → 200dpi px 값으로 바꾸는 짧은 함수
    truth = {"mcq": {}, "sid": []}

    def fill(center, r):
        """동그라미 하나를 펜으로 칠한 것처럼 그림 (draw_marks 안에서만 쓰는 보조 함수).

        중심을 표준편차 0.25mm만큼 흔들고, 반지름 80~105% 크기의 약간 찌그러진 타원을
        밝기 15~70(진한 회색~검정)으로 채움.

        매개변수:
            center: 동그라미 중심 [x, y] (mm)
            r: 동그라미 반지름 (mm)
        반환:
            None (바깥 함수의 img를 직접 수정)
        """
        cx = px(center[0] + rng.normal(0, 0.25))
        cy = px(center[1] + rng.normal(0, 0.25))
        rr = px(r) * rng.uniform(0.8, 1.05)
        tone = int(rng.uniform(15, 70))
        cv2.ellipse(img, (int(cx), int(cy)), (int(rr), int(rr * rng.uniform(0.85, 1.0))),
                    rng.uniform(0, 180), 0, 360, (tone,) * 3, -1, cv2.LINE_AA)

    # 객관식 마킹
    if True:
        for q in layout["mcq"]["questions"]:
            if rng.random() < 0.05:
                truth["mcq"][q["q"]] = None           # 빈 응답
                continue
            a = int(rng.integers(1, len(q["choices"]) + 1))
            truth["mcq"][q["q"]] = a
            c = q["choices"][a - 1]
            fill(c["center_mm"], c["radius_mm"])
    # 학번 마킹 (n = 학번 자리 수)
    if True:
        n = layout["student_id"]["digits"]
        digits = [int(d) for d in rng.integers(0, 10, n)]
        truth["sid"] = digits
        for b in layout["student_id"]["bubbles"]:
            if b["value"] == digits[b["digit_index"]]:
                fill(b["center_mm"], b["radius_mm"])
    # 주관식 칸에 가짜 손글씨 (무작위 영어 단어 3개) → 칸 잘라내기 결과 확인용
    words = "photosynthesis converts light energy into chemical glucose".split()
    for it in layout["short_answers"]["items"]:
        x, y, w, h = bbox_to_px(it["answer_bbox_mm"], DPI)
        s = " ".join(rng.choice(words, 3))
        cv2.putText(img, s, (x + 15, y + h // 2 + 12), cv2.FONT_HERSHEY_SCRIPT_SIMPLEX,
                    1.0, (40, 40, 60), 2, cv2.LINE_AA)
    return img, truth


# ---------- 가짜 스마트폰 사진 ----------
def simulate_photo(page: np.ndarray, lv: dict, rng) -> tuple[np.ndarray, np.ndarray]:
    """답안지 이미지를 '스마트폰으로 찍은 사진'처럼 망가뜨림.

    순서: 원근·회전으로 배경 위에 비스듬히 놓기 → 조명 기울기(+그림자) → 흐림 → 노이즈 → JPEG 압축.
    왜곡 정도는 lv(LEVELS의 한 난이도)로 정함.

    매개변수:
        page: 마킹된 답안지 이미지 (BGR 배열, 200dpi)
        lv: 난이도 설정 dict (예: LEVELS["medium"]). 키 의미는 LEVELS 주석 참고
        rng: 난수 생성기
    반환:
        (photo, Hs)
          photo: 가짜 사진 (BGR 배열, 4032 x 3024 또는 3024 x 4032 px)
          Hs: 답안지 이미지 좌표 → 사진 좌표 변환 행렬 (3x3). 정렬 오차를 계산할 때 '정답 변환'으로 사용
    """
    H0, W0 = page.shape[:2]   # 답안지 이미지 높이, 너비 (px)
    CW, CH = (3024, 4032) if H0 > W0 else (4032, 3024)    # 세로/가로에 맞춰 폰을 돌려 찍었다고 가정
    # 용지가 사진 화면의 78~90%를 차지하도록 크기 결정, 중심은 화면 가운데에서 ±80px 이동
    scale = min(CH / H0, CW / W0) * rng.uniform(0.78, 0.9)
    w, h = W0 * scale, H0 * scale
    cx, cy = CW / 2 + rng.uniform(-80, 80), CH / 2 + rng.uniform(-80, 80)
    # 용지 네 모서리 위치(중심 기준) → 모서리마다 jitter만큼 흔들기(원근) → rot 범위에서 회전 → 화면 위치로 이동
    base = np.array([[-w / 2, -h / 2], [w / 2, -h / 2], [w / 2, h / 2], [-w / 2, h / 2]])
    base += rng.uniform(-1, 1, (4, 2)) * lv["jitter"] * np.array([w, h])
    t = np.deg2rad(rng.uniform(-lv["rot"], lv["rot"]))
    R = np.array([[np.cos(t), -np.sin(t)], [np.sin(t), np.cos(t)]])
    dst = (base @ R.T + [cx, cy]).astype(np.float32)
    src = np.array([[0, 0], [W0, 0], [W0, H0], [0, H0]], dtype=np.float32)
    Hs = cv2.getPerspectiveTransform(src, dst)   # 원래 네 모서리(src) → 사진 속 네 모서리(dst) 변환 행렬

    # 배경(책상) = 무작위 단색 + 약한 노이즈. 변환한 용지를 mask(용지가 있는 영역)에만 덮어씀
    bg = np.full((CH, CW, 3), rng.integers(70, 150, 3), np.uint8)
    bg = cv2.add(bg, rng.normal(0, 6, bg.shape).astype(np.int16).clip(0, 255).astype(np.uint8))
    warped = cv2.warpPerspective(page, Hs, (CW, CH), flags=cv2.INTER_LINEAR)
    mask = cv2.warpPerspective(np.full((H0, W0), 255, np.uint8), Hs, (CW, CH))
    photo = np.where(mask[..., None] > 127, warped, bg).astype(np.float32)

    # 조명 기울기 (+ 그림자)
    # g: 무작위 방향으로 0→1로 변하는 밝기 기울기 → light: 가장 어두운 곳 lv["light"] ~ 가장 밝은 곳 1.0
    # shadow: 무작위 위치를 중심으로 최대 35% 어두워지는 타원형 얼룩 (가우시안 모양)
    ang = rng.uniform(0, 2 * np.pi)
    yy, xx = np.mgrid[0:CH, 0:CW].astype(np.float32)
    g = (np.cos(ang) * xx / CW + np.sin(ang) * yy / CH)
    g = (g - g.min()) / (g.max() - g.min())
    light = lv["light"] + (1 - lv["light"]) * g
    if lv["shadow"]:
        sx, sy = rng.uniform(0.2, 0.8) * CW, rng.uniform(0.2, 0.8) * CH
        light *= 1 - 0.35 * np.exp(-(((xx - sx) / (0.25 * CW)) ** 2 + ((yy - sy) / (0.2 * CH)) ** 2))
    photo *= light[..., None]
    # 흐림(초점 안 맞음) → 노이즈(센서 잡음) → 0~255로 자르기
    photo = cv2.GaussianBlur(photo, (0, 0), lv["blur"])
    photo += rng.normal(0, lv["noise"], photo.shape)
    photo = photo.clip(0, 255).astype(np.uint8)
    # JPEG로 저장했다가 다시 읽기 → 실제 사진 파일처럼 압축 손상을 넣음
    ok, enc = cv2.imencode(".jpg", photo, [cv2.IMWRITE_JPEG_QUALITY, lv["jpeg"]])
    decoded = cv2.imdecode(enc, cv2.IMREAD_COLOR)
    assert ok and decoded is not None
    return decoded, Hs


# ---------- 시각화 ----------
def overlay(warped, layout, answers):
    """정렬된 이미지 위에 양식 좌표(칸 위치)를 그려서 눈으로 확인하는 그림 만들기.

    색 (OpenCV는 BGR 순서): 판독된 답 = 초록 굵은 원, 나머지 객관식·학번 칸 = 파란 원,
    주관식 답 칸 = 빨간 사각형, QR 위치 = 보라 사각형.
    그린 원이 종이의 동그라미와 정확히 겹치면 좌표·정렬이 맞는 것.

    매개변수:
        warped: P1 정렬 결과 이미지 (BGR 배열, 200dpi). 원본은 바뀌지 않음
        layout: 양식 좌표 dict
        answers: {문항: 판독된 선지 번호 또는 None} (예: {1: 3, 2: None})
    반환:
        칸 위치를 그린 새 이미지 (BGR 배열)
    """
    vis = warped.copy()
    for q in layout["mcq"]["questions"]:
        for c in q["choices"]:
            cx, cy = (int(mm_to_px(v, DPI)) for v in c["center_mm"])
            chosen = answers.get(q["q"]) == c["choice"]
            cv2.circle(vis, (cx, cy), int(mm_to_px(c["radius_mm"], DPI)),
                       (0, 200, 0) if chosen else (255, 140, 0), 3 if chosen else 1)
    for b in layout["student_id"]["bubbles"]:
        cx, cy = (int(mm_to_px(v, DPI)) for v in b["center_mm"])
        cv2.circle(vis, (cx, cy), int(mm_to_px(b["radius_mm"], DPI)), (255, 140, 0), 1)
    for it in layout["short_answers"]["items"]:
        x, y, w, h = bbox_to_px(it["answer_bbox_mm"], DPI)
        cv2.rectangle(vis, (x, y), (x + w, y + h), (0, 0, 255), 3)
    x, y, w, h = bbox_to_px(layout["frame"]["qr"]["bbox_mm"], DPI)
    cv2.rectangle(vis, (x, y), (x + w, y + h), (200, 0, 200), 3)
    return vis


def crop_montage(crops, max_q=20):
    """잘라낸 객관식 칸 이미지들을 한 장의 모음 그림으로 붙이기.

    문항 하나 = 한 줄 (선지 칸을 64x64px로 줄여 가로로 나란히, 둘레에 흰 여백 2px).
    잘라내기가 칸 위치에 맞게 됐는지 한눈에 보는 용도.

    매개변수:
        crops: crop_regions()의 결과 dict. crops["mcq"] = {문항: [선지별 칸 이미지]} 사용
        max_q: 앞에서부터 몇 문항까지 붙일지 (기본 20)
    반환:
        모음 이미지 (BGR 배열). 객관식 칸이 하나도 없으면 None
    """
    rows = []
    for q in sorted(crops["mcq"])[:max_q]:
        cells = [cv2.resize(c, (64, 64), interpolation=cv2.INTER_AREA) for c in crops["mcq"][q]]
        rows.append(cv2.copyMakeBorder(np.hstack(cells), 2, 2, 2, 2, cv2.BORDER_CONSTANT, value=(255, 255, 255)))
    return np.vstack(rows) if rows else None


def main():
    """명령행 옵션을 읽어 스모크 테스트 전체를 실행하고 요약 표를 출력.

    단계는 모듈 설명 [내부 처리 순서] 참고.

    매개변수:
        없음 (명령행 옵션을 sys.argv에서 읽음)
    반환:
        None. 결과는 터미널 표와 --out 폴더의 파일로 나옴
    예외:
        AssertionError: 양식·PDF를 못 찾았거나, PDF 이미지 크기가 양식의 기준 크기와 다를 때
        SystemExit: PDF를 이미지로 바꿀 도구가 없을 때 (pdf_to_bgr)
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--num-questions", type=int, default=45)
    ap.add_argument("--num-choices", type=int, default=5)
    ap.add_argument("--orientation", default="portrait", choices=["portrait", "landscape"])
    ap.add_argument("--trials", type=int, default=10, help="난이도별 반복 횟수")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="smoke_out")
    ap.add_argument("--levels", default="easy,medium,hard")
    a = ap.parse_args()

    # 웹에서 만드는 것과 같은 함수로 답안지 생성 (storage/에 저장됨)
    exam = service.create_answer_sheet(a.num_questions, a.num_choices, a.orientation,
                                       "스모크 테스트", "컴퓨터비전")
    layout = service.load_template(exam["template_id"], exam["template_version"])
    pdf = service.exam_pdf_path(exam["exam_id"])
    assert layout is not None and pdf is not None
    page = pdf_to_bgr(pdf)   # 빈 답안지 이미지 (200dpi)
    # 렌더링한 이미지 크기가 양식의 기준 크기(세로 2339 x 1654 px 등)와 같은지 확인
    exp = (layout["frame"]["canonical"]["height_px"], layout["frame"]["canonical"]["width_px"])
    assert page.shape[:2] == exp, f"렌더 크기 {page.shape[:2]} != canonical {exp}"
    os.makedirs(a.out, exist_ok=True)
    rng = np.random.default_rng(a.seed)

    # 정렬 오차 측정용 기준점: 모든 마킹 칸 중심
    pts = [b["center_mm"] for b in all_bubbles(layout)]
    pts_px = (np.array(pts, dtype=np.float32) / 25.4 * DPI).reshape(-1, 1, 2)

    rows = []   # 사진 한 장마다의 측정값 dict 목록 → report.json
    levels = a.levels.split(",")
    for lvname in levels:
        lv = LEVELS[lvname]
        for t in range(a.trials):
            marked, truth = draw_marks(page, layout, rng)
            photo, Hs = simulate_photo(marked, lv, rng)
            rec = {"level": lvname, "trial": t}   # 이 사진의 측정값 (아래에서 하나씩 추가)
            t0 = time.perf_counter()
            # P1 정렬. 실패하면 사유 기록 + 실패 사진 저장 후 다음 사진으로
            try:
                res = align(photo)
            except AlignError as e:
                rec.update(ok=False, error=str(e)); rows.append(rec)
                cv2.imwrite(f"{a.out}/FAIL_{lvname}_{t}.jpg", cv2.resize(photo, None, fx=0.25, fy=0.25))
                continue
            rec["align_ms"] = round((time.perf_counter() - t0) * 1000)
            # QR로 양식 찾기: QR의 시험 ID가 이번 시험과 같을 때만 그 양식 좌표를 불러옴
            # 기록값: 마커 수, QR 성공, 방향 일치, 마커 재투영 오차(px), 선명도(blur_var, 낮을수록 흐림)
            lay = (service.load_template(res.qr_info["template_id"], res.qr_info["template_version"])
                   if res.qr_info and res.qr_info["exam_id"] == exam["exam_id"] else None)
            rec.update(ok=True, markers=len(res.found_ids), qr_ok=lay is not None,
                       orientation_ok=res.orientation == layout["orientation"],
                       marker_reproj_px=round(res.reproj_err_px, 2), blur_var=round(res.blur_var, 1))
            lay = lay or layout   # QR 실패 시에도 정렬 평가는 계속

            # 칸 중심 정렬 오차: 원래 위치 → (가짜 촬영) → (추정 정렬) → 비교
            # Hs(가짜 촬영)로 사진에 옮긴 칸 중심을 res.H(추정 정렬)로 되돌렸을 때 원래 위치와의 거리 → mm로 환산
            back = cv2.perspectiveTransform(cv2.perspectiveTransform(pts_px, Hs), res.H)
            d = np.linalg.norm((back - pts_px).reshape(-1, 2), axis=1)
            rec.update(center_err_mean_mm=round(float(d.mean()) * 25.4 / DPI, 3),
                       center_err_max_mm=round(float(d.max()) * 25.4 / DPI, 3))

            # 규칙 기반 판독 (pipeline/read.py, 모듈 3)
            # 문항마다 라벨과 비교 (빈칸 라벨은 [], 나머지는 [선지]와 같아야 정답)
            # answers: 그림 표시용 {문항: 읽은 선지 1개 또는 None}, correct: 라벨대로 읽은 문항 수
            read = read_sheet(res.warped, lay)
            answers, correct = {}, 0
            for q in read["mcq"]:
                t_ans = truth["mcq"][q["q"]]
                answers[q["q"]] = q["answer"][0] if q["answer"] and len(q["answer"]) == 1 else None
                correct += q["answer"] == ([] if t_ans is None else [t_ans])
            # 객관식 판독 정확도(0~1), 상태별 문항 수, 학번을 정확히 읽었는지
            rec["mcq_read_acc"] = round(correct / len(read["mcq"]), 3)
            rec["status"] = read["summary"]["status_counts"]
            rec["sid_ok"] = read["student_id"]["text"] == "".join(map(str, truth["sid"]))
            rows.append(rec)

            if t == 0:   # 난이도별 예시 이미지 저장
                # small(im, s): 이미지를 s배로 줄이는 짧은 함수 (파일 크기 줄이기)
                small = lambda im, s=0.3: cv2.resize(im, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)  # noqa
                cv2.imwrite(f"{a.out}/{lvname}_1_photo.jpg", small(photo, 0.25))
                cv2.imwrite(f"{a.out}/{lvname}_2_aligned_overlay.jpg", small(overlay(res.warped, lay, answers), 0.5))
                crops = crop_regions(res.warped, lay)
                mont = crop_montage(crops)
                if mont is not None:
                    cv2.imwrite(f"{a.out}/{lvname}_3_mcq_crops.png", mont)
                for no, im in crops["short_answers"].items():
                    cv2.imwrite(f"{a.out}/{lvname}_4_short_answer_{no}.jpg", small(im, 0.5))

    with open(f"{a.out}/report.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)

    # 난이도별 요약 표 출력 (열 의미는 모듈 설명 [결과 확인] 참고)
    print(f"\n양식 {layout['template_id']} v{layout['template_version']}  (난이도별 {a.trials}회)")
    print(f"{'level':7} {'정렬성공':>6} {'마커4개':>6} {'QR':>5} {'중심오차평균mm':>13} {'최대mm':>7} {'객관식판독':>9} {'학번':>5} {'ms':>5}")
    for lvname in levels:
        rs = [r for r in rows if r["level"] == lvname]   # 이 난이도의 전체 시도
        ok = [r for r in rs if r["ok"]]                  # 그중 정렬 성공한 것
        # f(k): 정렬 성공한 사진들의 측정값 k 평균 (성공이 없으면 nan)
        f = lambda k: np.mean([r[k] for r in ok]) if ok else float("nan")  # noqa: E731
        print(f"{lvname:7} {len(ok)}/{len(rs):<4} {sum(r['markers'] == 4 for r in ok):>6} "
              f"{sum(r['qr_ok'] and r['orientation_ok'] for r in ok):>5} {f('center_err_mean_mm'):>13.3f} "
              f"{max((r['center_err_max_mm'] for r in ok), default=float('nan')):>7.3f} "
              f"{f('mcq_read_acc'):>9.3f} {sum(r.get('sid_ok', False) for r in ok):>5} {f('align_ms'):>5.0f}")


if __name__ == "__main__":
    main()
