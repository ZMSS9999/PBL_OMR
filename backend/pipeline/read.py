# pyright: basic
"""파이프라인 P3 규칙 기반 마킹 판독 (로드맵 2-3).

[이 파일의 역할]
  - P1에서 반듯하게 편 답안지 이미지(warped)를 보고, 학생이 어느 동그라미를 칠했는지 읽어 냄
  - "규칙 기반" = 학습된 인공지능(CNN) 없이, 동그라미 안이 얼마나 검게 칠해졌는지(비율)와
    기준값만으로 판정한다는 뜻
  - 객관식 문항별 답과 학번을 읽음 (주관식은 아직 판독하지 않음)

[파이프라인에서 위치]
  P1 정렬(align.py) → P2 시험 확인(flow.py) → P3 판독 (이 파일) → P4 채점(grading/score.py) → P5 엑셀

[입력 → 출력]
  warped(P1 정렬 이미지) + layout(양식 좌표) ──read_sheet()──► read(dict)
      read["mcq"][i]        : {"q", "answer", "status", "needs_review", "fill", "cells"}
      read["student_id"]    : {"text", "raw", "status", "needs_review", "digits"}
      read["summary"]       : 상태별 개수, 검수 필요 여부
  → P4 채점(grading/score.py)은 answer와 student_id.text/raw만 씀

[판독 방식 2가지] (ReadConfig.method)
  - "darkness" (기본, 2026-11-06~): 잉크 농도 방식. 아래 [잉크 농도 방식] 참고
  - "fill"     (이전 방식): 이진화 후 검은 픽셀 비율. 아래 [1단계] 참고. 비교 실험용으로 남겨 둠

[잉크 농도 방식] (read_sheet_darkness)
  합성 데이터 1,004장(22.6만 칸) 실험으로 정함 (checkmate 문서/판독규칙_개선_실험 참고)
  1. 인쇄 마스크(L1): 같은 선지 번호(객관식) / 같은 값(학번) 칸들의 중앙값 이미지에서 진한 곳
     = 칸 안 인쇄 숫자·테두리. 마킹된 칸은 소수라 중앙값에서 빠짐 → 사진마다 스스로 맞춤
  2. 칸마다 인쇄 마스크를 뺀 픽셀로 3가지 값 계산 (잉크 농도 = 종이 대비 어두운 정도 = (종이 밝기 - 픽셀) / 종이 밝기, 0~1)
       g    : 안쪽 70% 원의 평균 잉크 농도     → 정상 마킹 0.76~1.0 / 흐림·지운 흔적 0.15~0.40 / 빈칸 0.00~0.09
       cov  : 안쪽 원에서 진한 픽셀(0.45 초과) 비율 → 얇게 채움·체크 구분
       ring : 칸 바깥 링(반지름 1.12~1.45배)의 진한 픽셀 비율 → 동그라미 표시 0.36~0.98 / 그 외 0.00~0.11
  3. 칸 상태: marked(g 또는 cov ≥ 0.60) / ambiguous(cov ≥ 0.25) / light(g ≥ 0.10, 연한 회색)
              / ring(안은 비고 바깥 링에 잉크) / blank
  4. 문항 판정 (decide_group_darkness)
       - ambiguous 칸 있음                → 보류 (얇게 채움·체크)
       - marked 칸 있음                   → 답 = marked 칸. 같은 문항의 light 칸은 지운 흔적으로 보고 무시
       - marked 없이 ring 칸 있음         → 보류 (동그라미 표시, CNN 전까지 사람 확인)
       - marked 없이 light 칸만 1개       → 흐린 마킹. faint_policy="accept"면 답으로, "review"면 보류
       - 아무것도 없음                    → 무응답. 단 과노출 사진이면 보류 (연한 마킹이 날아갔을 수 있음)
  5. 과노출 확인: 칸 주변 인쇄 잉크 대비(97백분위 잉크 농도)의 사진 전체 중앙값이 expo_min 미만이면
     summary["overexposed"] = True (합성 데이터: 정상 0.43~0.52, 너무 밝은 사진 0.24~0.43)
  6. 과노출 사진 처리 (expo_policy, 2026-11-07~ 3단계 방식 B)
       - 인쇄 잉크 대비 < expo_retake(임시 0.24) : 아주 밝음 → summary["retake"] = True → flow.py가 '재촬영 필요(너무 밝음)'로 처리
       - expo_retake ~ expo_min(0.42)            : 조금 밝음 → 채점은 하되 무응답 문항만 검수 (흐린 마킹이 날아갔을 수 있음)
       - expo_min 이상                            : 정상
       근거(합성 10,044장): 0.42 아래부터 흐린 마킹이 사라지기 시작 (0.40~0.42 39%, 0.31 아래 87~97%) → 0.42 아래에 안전한 값 없음.
       목표 재촬영률 5% 이하(kim, 11/6)에 맞춰 expo_retake를 정함. 합성에서 0.24 = 재촬영 약 1.8~8%. 실측 분포로 다시 정할 것
       - expo_policy="review": 재촬영 없이 0.42 미만은 모두 무응답 문항만 검수
  7. 부분 반사 (glare_min, 2026-11-07~): 사진 전체는 정상인데 문항 주변 인쇄 잉크 대비(local_contrast)만 낮으면
     그 문항은 판정과 상관없이 검수 (반사로 진한 마킹·동그라미까지 사라질 수 있음). 반사 문항이 glare_retake_frac(30%)를
     넘으면 장 전체 재촬영. 과노출 사진(6번)에는 적용 안 함

[1단계: 칸 하나 판정] (이전 방식 "fill")
  - 정렬 이미지 전체를 적응형 이진화 (주변 밝기 기준으로 흑백 구분 → 그림자·조명 차이에 강함)
  - 동그라미 안쪽 70% 원 안에서 검은 픽셀 비율(fill) 계산
      · 테두리 선은 안쪽 원 밖이라 빠짐
      · 칸 안 회색 숫자는 조금 잡힘 (빈칸 fill 0.00~0.14)
  - fill ≥ 0.60 → marked(마킹) / 0.25 ≤ fill < 0.60 → ambiguous(애매) / fill < 0.25 → blank(빈칸)

[0단계: 칸 위치 미세 보정] (refine_offsets)
  - 마커로 편 이미지에서도 칸이 layout 좌표와 1~2mm 어긋날 수 있음
      · 실제 인쇄본(OMR_A4_가로형_수정본_인쇄.pptx)은 객관식 칸이 팀 JSON보다 1.68mm 왼쪽에 있음
      · 프린터 확대·축소, 종이 휨 등
  - 블록(객관식 열 하나, 학번 칸 전체)마다 인쇄된 동그라미 테두리 모양을 이미지에서 찾아서
    가장 잘 겹치는 이동량(dx, dy)만큼 칸 좌표를 옮긴 뒤 판독함
  - 찾는 범위는 ±2.5mm, 단 칸 간격의 절반을 넘지 않게 (옆 칸으로 잘못 맞추지 않게)
  - 이동량은 read["offsets_mm"]에 기록 (검수·디버깅용)

[2단계: 문항 하나 판정] (칸 상태 ≠ 문항 답, 3주차 회의 형식)
  answer = [3]     정상 응답 1개              status "ok"
  answer = [2, 5]  복수 응답                  status "multi"
  answer = []      무응답                     status "blank"
  answer = None    판정 보류 (애매한 칸 있음)  status "ambiguous", needs_review=True
  → 애매한 칸이 하나라도 있으면 자동 확정 안 함 ("확실한 것은 자동, 애매한 것만 사람에게")
  → 3단계(CNN)에서는 애매한 칸만 CNN으로 넘기면 됨 = step_read 자리에서 교체

[기준값 0.60 / 0.25 근거] (임시값)
  - 가상 사진(tools/demo_end_to_end.py)에서 빈칸 0.00~0.14, 진한 마킹 0.79~1.00, 반만 칠한 칸 0.47~0.57
  - 반칸을 정상 마킹으로 볼지 미정 → 반칸이 '애매 → 검수'로 가도록 잡음
  - 실제 촬영 데이터가 생기면 개발 검증 데이터로 다시 정할 것 (ReadConfig로 바꿔 넣으면 됨)

[다른 파일과의 관계]
  - 이 파일을 부르는 쪽 : pipeline/flow.py(step_read), app/grading_service.py(ReadConfig),
                          tools/smoke_test.py·tools/synth_eval.py(read_sheet, ReadConfig)
  - 이 파일이 부르는 쪽 : sheetgen/frame.py(CANONICAL_DPI), sheetgen/layout.py(mm_to_px)
  - layout에서 쓰는 키 : mcq.questions[].q / .choices[].choice·center_mm·radius_mm,
                          student_id.digits / .bubbles[].digit_index·value·center_mm·radius_mm,
                          template_id, template_version

[주요 함수/클래스]
  - MARKED / AMBIGUOUS / BLANK : 칸 상태 문자열 상수
  - ReadConfig       : 판독 기준값 묶음 (마킹 기준, 애매 기준, 이진화 설정 등)
  - binarize()       : 정렬 이미지 → 흑백(잉크/종이) 이미지
  - fill_ratio()     : 동그라미 하나의 잉크 비율(0~1) 계산
  - cell_state()     : 잉크 비율 → 칸 상태 (marked / ambiguous / blank)
  - decide_group()   : 칸 상태 여러 개 → 한 문항(또는 학번 한 자리)의 답과 상태
  - read_mcq()       : 객관식 전 문항 판독
  - read_student_id(): 학번 판독
  - refine_offsets() : 블록별 칸 위치 미세 보정 (인쇄 위치 차이 흡수)
  - read_sheet()     : P3 메인 함수. 답안지 한 장 전체 판독 → read dict (method에 따라 아래 함수로 넘김)
  - read_sheet_darkness() : 잉크 농도 방식 판독 (기본)
  - cell_state_darkness() / decide_group_darkness() : 잉크 농도 방식의 칸 상태 / 문항 판정
  - question_confidence()  : 문항 신뢰도 0~1 (기준값까지 거리, conf_min 미만이면 검수)

[사용 예]
  from pipeline.read import read_sheet, ReadConfig
  read = read_sheet(res.warped, layout)                         # res = align() 결과
  read["mcq"][0]          # 예: {"q": 1, "answer": [3], "status": "ok", ...}
  read["student_id"]["text"]   # 예: "2026103456" (못 읽으면 None)
  read2 = read_sheet(res.warped, layout, ReadConfig(method="fill"))    # 이전 방식으로 비교
  read3 = read_sheet(res.warped, layout, ReadConfig(faint_policy="review"))   # 흐린 마킹은 검수로
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import cv2
import numpy as np

from sheetgen import frame as F
from sheetgen.layout import mm_to_px

MARKED, AMBIGUOUS, BLANK = "marked", "ambiguous", "blank"   # 칸 상태 3가지 (마킹 / 애매 / 빈칸)
RETAKE_GLARE_MSG = "답안지 일부에 빛 반사가 있어 마킹이 보이지 않을 수 있음. 반사가 없는 곳에서 다시 찍어 주세요"
RETAKE_BRIGHT_MSG = "사진이 너무 밝아 연한 마킹이 보이지 않을 수 있음. 조명 반사를 피해 다시 찍어 주세요"
LIGHT, RING = "light", "ring"   # 잉크 농도 방식에서만 쓰는 칸 상태 (연한 회색 / 칸 바깥 링 잉크)


@dataclass
class ReadConfig:
    """판독 설정값 묶음. 실험할 때 이 값만 바꿔 넣어 비교 (예: ReadConfig(marked_min=0.5)).

    read_sheet()에 넘기지 않으면 아래 기본값을 씀. 판독 결과 read["read_config"]에
    그대로 기록되므로 나중에 어떤 기준으로 읽었는지 확인 가능함.
    """
    marked_min: float = 0.60       # 이 비율 이상이면 마킹 (0~1 비율)
    ambiguous_min: float = 0.25    # 이 비율 이상 marked_min 미만이면 애매 (0~1 비율)
    inner_ratio: float = 0.70      # 동그라미 반지름 중 판정에 쓰는 안쪽 비율 (0.70 = 반지름의 70% 원만 봄 → 테두리 선 제외)
    block_size: int = 75           # 적응형 이진화 창 크기(px, 홀수). 200dpi에서 약 9.5mm
    offset_c: int = 12             # 적응형 이진화 상수 (클수록 옅은 자국을 덜 잡음)
    dpi: int = F.CANONICAL_DPI     # warped 이미지 해상도 (mm → px 변환에 사용, 기본 200)
    refine_max_mm: float = 2.5     # 칸 위치 미세 보정 최대 이동량 (mm). 0이면 보정 안 함
    # ---- 잉크 농도 방식("darkness") 설정. 근거는 맨 위 [잉크 농도 방식] 참고 ----
    method: str = "darkness"       # "darkness"(잉크 농도 방식, 기본) / "fill"(이전 방식)
    solid_min: float = 0.60        # g 또는 cov가 이 값 이상이면 marked
    partial_min: float = 0.25      # cov가 이 값 이상이면 ambiguous (얇게 채움·체크)
    light_min: float = 0.10        # g가 이 값 이상이면 light (흐림·지운 흔적). 빈칸 99.9%는 0.09 이하
    ring_min: float = 0.15         # ring이 이 값 이상이면 ring (동그라미 표시)
    ink_dark: float = 0.45         # 진한 픽셀로 보는 잉크 농도 (cov, ring 계산용)
    print_dark: float = 0.15       # 인쇄 마스크로 보는 중앙값 잉크 농도
    expo_min: float = 0.42         # 인쇄 잉크 대비 중앙값이 이보다 낮으면 과노출
    faint_policy: str = "accept"   # 흐린 마킹(light 1개) 처리: "accept"(답으로 인정) / "review"(검수)
    expo_retake: float = 0.24      # 인쇄 잉크 대비가 이보다 낮으면 재촬영 (임시값, 목표 재촬영률 5%로 실측에서 다시 정함)
    glare_min: float = 0.38        # 문항 주변 인쇄 잉크 대비가 이보다 낮으면 부분 반사로 보고 검수 (0 = 끔)
                                   # 합성 가공 실험(11/7): 원본 문항 최소 0.406, 반사 사진 오판독 464 → 18문항
    glare_retake_frac: float = 0.3 # 부분 반사 문항이 이 비율을 넘으면 장 전체 재촬영
    blur_min: float = 100.0        # 사진 선명도(blur_var, align.py)가 이보다 낮으면 재촬영 (flow.py가 확인)
                                   # 합성 가공 실험(11/7): 원본 최소 142, 손떨림 8px 이상에서 오판독 → 100. 사진 해상도에 따라 값이 달라져 실측으로 다시 정할 것
    conf_min: float = 0.0          # 문항 신뢰도가 이보다 낮으면 검수 (0 = 끔. 합성: 0.2면 검수 +4.1%p, 오판독 29 → 1문항)
    expo_policy: str = "retake"    # 과노출 사진 처리: "retake"(아주 밝으면 재촬영 + 조금 밝으면 무응답 검수) / "review"(재촬영 없이 무응답 검수)


def binarize(warped_bgr: np.ndarray, cfg: ReadConfig) -> np.ndarray:
    """적응형 이진화 → 흑백 이미지 (잉크=255, 종이=0).
    픽셀마다 주변 block_size 창의 가중 평균보다 offset_c 이상 어두우면 잉크로 봄
    → 사진 한쪽이 어두워도(그림자) 그 주변 기준으로 판단하므로 영향이 적음.

    매개변수:
        warped_bgr: P1 정렬 이미지 (BGR 3채널 또는 이미 흑백인 1채널 배열)
        cfg: 판독 설정 (block_size, offset_c 사용)
    반환:
        warped와 같은 크기의 1채널 uint8 배열. 잉크(검은 자국) = 255, 종이 = 0
    """
    # 컬러면 흑백으로 바꾸고, 이미 흑백(2차원 배열)이면 그대로 사용
    gray = cv2.cvtColor(warped_bgr, cv2.COLOR_BGR2GRAY) if warped_bgr.ndim == 3 else warped_bgr
    # THRESH_BINARY_INV: 어두운 잉크 쪽을 255(흰색)로 뒤집음 → 뒤에서 0이 아닌 픽셀 수 = 잉크 양
    return cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV,
                                 cfg.block_size, cfg.offset_c)


def fill_ratio(binv: np.ndarray, bubble: dict[str, Any], cfg: ReadConfig) -> float:
    """동그라미 안쪽 원 영역의 잉크 비율 (0~1). 0 = 깨끗, 1 = 꽉 칠함.

    동그라미 반지름의 inner_ratio(70%)만큼 작은 원 안만 봄
    → 인쇄된 동그라미 테두리 선이 비율에 섞이지 않게 하기 위함.

    매개변수:
        binv: binarize() 결과 (잉크=255, 종이=0)
        bubble: layout의 동그라미 칸 하나. "center_mm"([x, y] mm), "radius_mm"(mm) 키를 씀
                예: {"q": 1, "choice": 3, "center_mm": [40.2, 88.5], "radius_mm": 2.1, ...}
        cfg: 판독 설정 (inner_ratio, dpi 사용)
    반환:
        잉크 픽셀 비율 float (0.0~1.0). 예: 빈칸 0.05, 진한 마킹 0.92
        원이 이미지 밖으로 나가면 0.0
    """
    cx, cy = (mm_to_px(v, cfg.dpi) for v in bubble["center_mm"])          # 원 중심 px
    r = max(1, int(mm_to_px(bubble["radius_mm"] * cfg.inner_ratio, cfg.dpi)))   # 판정용 안쪽 원 반지름 px (최소 1)
    x0, y0 = int(round(cx)) - r, int(round(cy)) - r      # 원을 감싸는 정사각형의 왼쪽 위
    h, w = binv.shape[:2]
    if x0 < 0 or y0 < 0 or x0 + 2 * r + 1 > w or y0 + 2 * r + 1 > h:
        return 0.0                                        # 이미지 밖 (정상 정렬이면 발생 안 함)
    patch = binv[y0:y0 + 2 * r + 1, x0:x0 + 2 * r + 1]   # 원을 감싸는 정사각형 영역 (한 변 2r+1 px)
    mask = np.zeros_like(patch)
    cv2.circle(mask, (r, r), r, 255, -1)                  # 원 모양 마스크 (정사각형 모서리 제외)
    # 원 안 픽셀 중 잉크(0이 아닌) 픽셀 수 ÷ 원 안 전체 픽셀 수
    return float(np.count_nonzero(patch[mask > 0]) / np.count_nonzero(mask))


def cell_state(ratio: float, cfg: ReadConfig) -> str:
    """채움 비율 → 칸 상태 (marked / ambiguous / blank).

    매개변수:
        ratio: fill_ratio() 결과 (0~1)
        cfg: 판독 설정 (marked_min, ambiguous_min 사용)
    반환:
        "marked"(ratio ≥ 0.60) / "ambiguous"(0.25 ≤ ratio < 0.60) / "blank"(ratio < 0.25)
        (기준값은 기본 설정 기준. 예: 0.47 → "ambiguous")
    """
    if ratio >= cfg.marked_min:
        return MARKED
    if ratio >= cfg.ambiguous_min:
        return AMBIGUOUS
    return BLANK


def decide_group(states: list[str], values: list[int]) -> tuple[list[int] | None, str]:
    """칸 상태 묶음(한 문항 또는 학번 한 자리) → (답, 상태).
    values: 각 칸이 뜻하는 값 (객관식 1~k, 학번 0~9)
    우선순위: 애매한 칸이 있으면 무조건 보류 → 마킹 0개 = 무응답 → 1개 = 정상 → 2개 이상 = 복수

    매개변수:
        states: 칸별 상태 목록 (cell_state() 결과). 예: ["blank", "blank", "marked", "blank", "blank"]
        values: states와 같은 순서로, 각 칸이 뜻하는 값. 예: [1, 2, 3, 4, 5]
    반환:
        (답, 상태) 튜플
        예: ([3], "ok") / ([2, 5], "multi") / ([], "blank") / (None, "ambiguous")
    """
    if AMBIGUOUS in states:
        return None, "ambiguous"
    marked = [v for v, s in zip(values, states) if s == MARKED]   # 마킹된 칸의 값만 모음
    if not marked:
        return [], "blank"
    return marked, "ok" if len(marked) == 1 else "multi"


def read_mcq(binv: np.ndarray, layout: dict[str, Any], cfg: ReadConfig) -> list[dict[str, Any]]:
    """객관식 전 문항 판독. fill·cells(칸별 값)도 같이 남김 → 검수 화면, 기준값 조정 실험에 사용.

    매개변수:
        binv: binarize() 결과
        layout: 양식 좌표 dict (layout["mcq"]["questions"] 사용)
        cfg: 판독 설정
    반환:
        문항 순서대로 dict 목록. 각 dict의 키는 아래와 같음
          q            : 문항 번호 (1부터)
          answer       : [3] / [2, 5] / [] / None(판정 보류)
          status       : "ok" / "multi" / "blank" / "ambiguous"
          needs_review : 사람 검수가 필요하면 True (status가 "ambiguous"일 때)
          fill         : 선지별 잉크 비율 (소수 3자리, 예: [0.02, 0.01, 0.91, 0.03, 0.0])
          cells        : 선지별 칸 상태 (예: ["blank", "blank", "marked", "blank", "blank"])
    """
    out: list[dict[str, Any]] = []
    for q in layout["mcq"]["questions"]:
        ratios = [fill_ratio(binv, c, cfg) for c in q["choices"]]   # 선지별 잉크 비율
        states = [cell_state(r, cfg) for r in ratios]               # 선지별 칸 상태
        answer, status = decide_group(states, [c["choice"] for c in q["choices"]])
        out.append({
            "q": q["q"],
            "answer": answer,                       # [3] / [2, 5] / [] / None(판정 보류)
            "status": status,                       # ok / multi / blank / ambiguous
            "needs_review": status == "ambiguous",
            "fill": [round(r, 3) for r in ratios],
            "cells": states,
        })
    return out


def read_student_id(binv: np.ndarray, layout: dict[str, Any], cfg: ReadConfig) -> dict[str, Any]:
    """학번 판독: 자리마다 0~9 중 마킹된 숫자 하나.
    text: 모든 자리가 정상일 때만 학번 문자열, 아니면 None (→ 검수)
    raw : 읽은 그대로, 못 읽은 자리는 '?' (예: "20261?3456") → 검수 화면에 참고로 표시

    매개변수:
        binv: binarize() 결과
        layout: 양식 좌표 dict (layout["student_id"]의 "digits"(자릿수), "bubbles" 사용)
        cfg: 판독 설정
    반환:
        dict. 키는 아래와 같음
          text         : 학번 문자열 (예: "2026103456"). 한 자리라도 정상이 아니면 None
          raw          : 자리별로 읽은 숫자, 정상이 아닌 자리는 "?" (예: "20261?3456")
          status       : "ok"(전 자리 정상) / "blank"(전 자리 무응답) / "review"(그 외 → 검수)
          needs_review : status가 "ok"가 아니면 True
          digits       : 자리별 상세 [{"digit_index", "value", "status", "fill"}, ...]
                         value는 decide_group()의 답 (예: [7], [], None)
    """
    sid = layout["student_id"]
    digits: list[dict[str, Any]] = []   # 자리별 판독 결과
    for di in range(sid["digits"]):
        # di번째 자리의 동그라미 10개를 값(0~9) 순서로 정렬
        bs = sorted((b for b in sid["bubbles"] if b["digit_index"] == di), key=lambda b: b["value"])
        ratios = [fill_ratio(binv, b, cfg) for b in bs]
        states = [cell_state(r, cfg) for r in ratios]
        value, status = decide_group(states, [b["value"] for b in bs])
        digits.append({"digit_index": di, "value": value, "status": status,
                       "fill": [round(r, 3) for r in ratios]})
    # 정상("ok")인 자리는 숫자, 나머지(무응답·복수·애매)는 "?"로 이어 붙인 문자열
    text = "".join(str(d["value"][0]) if d["status"] == "ok" and d["value"] else "?" for d in digits)
    # 자리별 상태를 모아서 학번 전체 상태 결정
    statuses = {d["status"] for d in digits}
    if statuses == {"ok"}:
        status = "ok"
    elif statuses == {"blank"}:
        status = "blank"
    else:
        status = "review"
    return {"text": text if status == "ok" else None, "raw": text, "status": status,
            "needs_review": status != "ok", "digits": digits}


def _block_offset(binv: np.ndarray, bubbles: list[dict[str, Any]], cfg: ReadConfig) -> tuple[float, float]:
    """동그라미 묶음(블록) 하나의 위치 보정량 (dx, dy) mm.

    블록의 동그라미 테두리를 그린 틀(template)을 이진 이미지 위에서 조금씩 옮겨 보며
    인쇄된 테두리와 가장 많이 겹치는 위치를 찾음 (cv2.matchTemplate).
    빈 칸이 대부분이라 마킹된 칸이 몇 개 있어도 결과가 흔들리지 않음.

    매개변수:
        binv: binarize() 결과 (잉크=255, 종이=0)
        bubbles: 이 블록의 동그라미 칸 목록 (layout의 choices 또는 student_id.bubbles 항목)
        cfg: 판독 설정 (refine_max_mm, dpi 사용)
    반환:
        (dx, dy) mm. 칸을 이만큼 옮기면 인쇄된 동그라미와 맞음. 보정할 수 없으면 (0.0, 0.0)
    """
    pts = np.array([b["center_mm"] for b in bubbles], dtype=np.float64)   # 칸 중심 mm (N, 2)
    if len(pts) < 2 or cfg.refine_max_mm <= 0:
        return 0.0, 0.0
    # 찾는 범위: refine_max_mm, 단 가장 가까운 두 칸 간격의 45% 이하 (옆 칸에 맞춰지는 것 방지)
    d = np.linalg.norm(pts[:, None, :] - pts[None, :, :], axis=2)
    np.fill_diagonal(d, np.inf)
    s = int(mm_to_px(min(cfg.refine_max_mm, 0.45 * float(d.min())), cfg.dpi))   # 찾는 범위 px
    if s < 1:
        return 0.0, 0.0
    r_max = max(b["radius_mm"] for b in bubbles)
    x0, y0 = (int(mm_to_px(v - r_max - 1, cfg.dpi)) for v in pts.min(axis=0))   # 블록 영역 왼쪽 위 px
    x1, y1 = (int(mm_to_px(v + r_max + 1, cfg.dpi)) for v in pts.max(axis=0))   # 블록 영역 오른쪽 아래 px
    h, w = binv.shape[:2]
    if x0 - s < 0 or y0 - s < 0 or x1 + s > w or y1 + s > h:
        return 0.0, 0.0                                   # 블록이 이미지 가장자리에 붙음 (정상 양식이면 없음)
    tmpl = np.zeros((y1 - y0, x1 - x0), np.float32)       # 동그라미 테두리 틀
    t = max(2, int(mm_to_px(0.3, cfg.dpi)))               # 테두리 두께 px (인쇄 선보다 약간 두껍게)
    for b in bubbles:
        cx, cy = (mm_to_px(v, cfg.dpi) for v in b["center_mm"])
        cv2.circle(tmpl, (int(round(cx)) - x0, int(round(cy)) - y0),
                   int(round(mm_to_px(b["radius_mm"], cfg.dpi))), 1.0, t)
    img = binv[y0 - s:y1 + s, x0 - s:x1 + s].astype(np.float32)
    score = cv2.matchTemplate(img, tmpl, cv2.TM_CCOEFF)  # (2s+1, 2s+1): 이동량별 겹침 점수
    _, _, _, (bx, by) = cv2.minMaxLoc(score)              # 가장 높은 점수 위치
    k = 25.4 / cfg.dpi                                    # px → mm
    return round((bx - s) * k, 2), round((by - s) * k, 2)


def refine_offsets(binv: np.ndarray, layout: dict[str, Any], cfg: ReadConfig) -> tuple[dict[str, Any], dict[str, Any]]:
    """블록별로 칸 위치를 보정한 layout 사본과 보정량을 돌려줌 (원본 layout은 안 바꿈).

    블록 = 객관식 열 하나(첫 선지 x 좌표가 같은 문항들) / 학번 칸 전체.

    매개변수:
        binv: binarize() 결과
        layout: 양식 좌표 dict
        cfg: 판독 설정
    반환:
        (보정된 layout, 보정량 dict)
        보정량 예: {"mcq": [[-1.65, 0.1], [-1.7, 0.0], [-1.6, 0.05]], "student_id": [0.05, -0.1]}
                   (mcq는 왼쪽 열부터, 값은 [dx, dy] mm)
    """
    def moved(b: dict[str, Any], dx: float, dy: float) -> dict[str, Any]:
        """칸 하나의 중심을 (dx, dy) mm 옮긴 사본."""
        return {**b, "center_mm": [b["center_mm"][0] + dx, b["center_mm"][1] + dy]}

    # 객관식: 첫 선지 x 좌표(0.1mm 단위로 반올림)가 같은 문항끼리 한 열
    cols: dict[float, list[dict[str, Any]]] = {}
    for q in layout["mcq"]["questions"]:
        cols.setdefault(round(q["choices"][0]["center_mm"][0], 1), []).append(q)
    shift: dict[int, tuple[float, float]] = {}   # 문항 번호 → (dx, dy)
    mcq_off = []
    for x in sorted(cols):
        dx, dy = _block_offset(binv, [c for q in cols[x] for c in q["choices"]], cfg)
        mcq_off.append([dx, dy])
        for q in cols[x]:
            shift[q["q"]] = (dx, dy)
    qs = [{**q, "choices": [moved(c, *shift[q["q"]]) for c in q["choices"]]} for q in layout["mcq"]["questions"]]

    sid = layout["student_id"]
    sdx, sdy = _block_offset(binv, sid["bubbles"], cfg)
    out = {**layout, "mcq": {**layout["mcq"], "questions": qs},
           "student_id": {**sid, "bubbles": [moved(b, sdx, sdy) for b in sid["bubbles"]]}}
    return out, {"mcq": mcq_off, "student_id": [sdx, sdy]}


# ---------------- 잉크 농도 방식 (method="darkness") ----------------
def _crop_darkness(gray: np.ndarray, bubble: dict[str, Any], half: int, cfg: ReadConfig) -> np.ndarray:
    """칸 중심 기준 (2*half) 정사각형 크롭의 잉크 농도 맵 (0~1, 1 = 종이 대비 가장 진함).

    종이 밝기 = 크롭의 90백분위 (크롭 대부분이 종이라서) → 그림자 진 칸은 그 주변 종이 기준으로 잼.

    매개변수:
        gray: 정렬 이미지 흑백 (uint8). 가장자리에 half만큼 흰색 여백을 붙인 것
        bubble: layout 칸 하나 ("center_mm" 사용)
        half: 크롭 반 변 길이 px
        cfg: 판독 설정 (dpi 사용)
    반환:
        (2*half, 2*half) float32 배열
    """
    cx = int(round(mm_to_px(bubble["center_mm"][0], cfg.dpi))) + half   # 여백 붙인 만큼 이동
    cy = int(round(mm_to_px(bubble["center_mm"][1], cfg.dpi))) + half
    c = gray[cy - half:cy + half, cx - half:cx + half].astype(np.float32)
    paper = max(float(np.percentile(c, 90)), 1.0)
    return np.clip((paper - c) / paper, 0, 1)


def _print_masks(crops: dict[Any, list[np.ndarray]], cfg: ReadConfig) -> dict[Any, np.ndarray]:
    """같은 묶음(선지 번호 / 학번 값) 크롭들의 중앙값 → 인쇄 마스크 (True = 인쇄된 숫자·테두리).

    마킹된 칸은 묶음 안에서 소수라 중앙값에 안 남음. 가장자리 흐림을 덮으려고 1px 넓힘.

    매개변수:
        crops: {묶음 키: 잉크 농도 크롭 목록}
        cfg: 판독 설정 (print_dark 사용)
    반환:
        {묶음 키: bool 배열}
    """
    out = {}
    for k, cs in crops.items():
        med = np.median(np.stack(cs), axis=0)
        out[k] = cv2.dilate((med > cfg.print_dark).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    return out


def _cell_values(dark: np.ndarray, pmask: np.ndarray, radius_px: float, cfg: ReadConfig) -> tuple[float, float, float]:
    """칸 하나의 (g, cov, ring). 인쇄 마스크 픽셀은 모두 뺌.

    매개변수:
        dark: _crop_darkness() 결과
        pmask: 이 칸 묶음의 인쇄 마스크
        radius_px: 칸 반지름 px
        cfg: 판독 설정 (inner_ratio, ink_dark 사용)
    반환:
        g    : 안쪽 원 평균 잉크 농도
        cov  : 안쪽 원 진한 픽셀 비율
        ring : 바깥 링(반지름 1.12~1.45배) 진한 픽셀 비율
    """
    s = dark.shape[0]
    yy, xx = np.mgrid[0:s, 0:s]
    dist = np.hypot(xx - s / 2 + 0.5, yy - s / 2 + 0.5)
    inner = (dist <= cfg.inner_ratio * radius_px) & ~pmask
    ring = (dist >= 1.12 * radius_px) & (dist <= 1.45 * radius_px) & ~pmask
    g = float(dark[inner].mean()) if inner.any() else 0.0
    cov = float((dark[inner] > cfg.ink_dark).mean()) if inner.any() else 0.0
    rg = float((dark[ring] > cfg.ink_dark).mean()) if ring.any() else 0.0
    return g, cov, rg


def cell_state_darkness(g: float, cov: float, ring: float, cfg: ReadConfig) -> str:
    """(g, cov, ring) → 칸 상태 (marked / ambiguous / light / ring / blank).

    매개변수:
        g, cov, ring: _cell_values() 결과
        cfg: 판독 설정 (solid_min, partial_min, light_min, ring_min)
    반환:
        칸 상태 문자열
    """
    if g >= cfg.solid_min or cov >= cfg.solid_min:
        return MARKED
    if cov >= cfg.partial_min:
        return AMBIGUOUS
    if g >= cfg.light_min:
        return LIGHT
    if ring >= cfg.ring_min:
        return RING
    return BLANK


def decide_group_darkness(states: list[str], values: list[int], cfg: ReadConfig,
                          overexposed: bool = False) -> tuple[list[int] | None, str, str]:
    """칸 상태 묶음 → (답, 상태, 이유). 답·상태는 decide_group()과 같은 형식.

    이유(reason): 검수로 보낸 이유나 특별 처리 내용 (검수 화면·실험 집계용)
      "partial" 얇게 채움·체크 / "ring" 동그라미 표시 / "light" 흐린 마킹(보류)
      "faint" 흐린 마킹을 답으로 인정 / "erasure" 지운 흔적을 무시함 / "overexposed" 과노출 사진의 무응답
      "low_conf" 신뢰도가 conf_min 미만이라 검수 / "glare" 문항 주변 부분 반사라 검수 (read_sheet_darkness에서 붙임)
      "" 특별한 것 없음

    매개변수:
        states: 칸 상태 목록 (cell_state_darkness() 결과)
        values: 칸 값 목록 (객관식 1~k, 학번 0~9)
        cfg: 판독 설정 (faint_policy 사용)
        overexposed: 사진이 과노출이면 True
    반환:
        (답, 상태, 이유) 예: ([3], "ok", "erasure") / (None, "ambiguous", "ring")
    """
    if AMBIGUOUS in states:
        return None, "ambiguous", "partial"
    solid = [v for v, s in zip(values, states) if s == MARKED]
    light = [v for v, s in zip(values, states) if s == LIGHT]
    if solid:
        return solid, "ok" if len(solid) == 1 else "multi", "erasure" if light else ""
    if RING in states:
        return None, "ambiguous", "ring"
    if light:
        if cfg.faint_policy == "accept" and len(light) == 1:
            return light, "ok", "faint"
        return None, "ambiguous", "light"
    if overexposed:
        return None, "ambiguous", "overexposed"
    return [], "blank", ""


def question_confidence(vals: list[tuple[float, float, float]], cfg: ReadConfig) -> float:
    """문항 신뢰도 0~1 (E6 S1). 답을 바꿀 수 있는 가장 가까운 기준값까지의 거리를 칸마다 재고, 가장 작은 값을 씀.

    칸별로 보는 기준값 (거리 ÷ 폭, 0~1로 자름)
      - marked 칸                 : solid_min까지 (폭 0.40). 덜 진하면 마킹이 빠질 수 있음
      - 진한 칸이 있는 문항의 나머지: solid_min·partial_min까지 (폭 0.25). 진해지면 이중 마킹, cov가 늘면 애매
                                    (옅은 칸은 어차피 지운 흔적으로 무시하므로 light_min은 안 봄)
      - 진한 칸 없고 light 칸      : light_min·solid_min·partial_min까지 (폭 0.15)
      - 진한 칸 없고 빈 칸         : light_min·partial_min·ring_min까지 (폭 0.10). 넘으면 흐린 마킹으로 읽힘
    합성 10,044장: 오판독 문항 중앙값 0.06, 정답 문항 중앙값 0.76, 둘을 가르는 AUC 0.97

    매개변수:
        vals: 칸별 (g, cov, ring) 목록 (_cell_values() 결과)
        cfg: 판독 설정
    반환:
        신뢰도 (1 = 기준값에서 충분히 멂, 0 = 기준값 바로 옆)
    """
    sm, pm, lm, rm = cfg.solid_min, cfg.partial_min, cfg.light_min, cfg.ring_min
    solid = any(g >= sm or c >= sm for g, c, _ in vals)
    conf = 1.0
    for g, c, r in vals:
        if g >= sm or c >= sm:
            v = (max(g, c) - sm) / 0.40
        elif solid:
            v = min(sm - g, pm - c) / 0.25
        elif g >= lm:
            v = min(g - lm, sm - g, pm - c) / 0.15
        else:
            v = min(lm - g, pm - c, rm - r) / 0.10
        conf = min(conf, max(0.0, min(1.0, v)))
    return conf


def read_sheet_darkness(warped_bgr: np.ndarray, layout: dict[str, Any], cfg: ReadConfig) -> dict[str, Any]:
    """잉크 농도 방식 P3 판독. 반환 형식은 read_sheet()와 같고, 아래 키가 더 붙음.

      mcq[i]["reason"], mcq[i]["confidence"], mcq[i]["local_contrast"], mcq[i]["g"], mcq[i]["cov"], mcq[i]["ring"]  (fill에는 g를 넣음)
      summary["overexposed"], summary["print_contrast"], summary["retake"], summary["retake_reason"]

    매개변수:
        warped_bgr: P1 정렬 이미지
        layout: 양식 좌표 dict
        cfg: 판독 설정
    반환:
        read dict
    """
    binv = binarize(warped_bgr, cfg)
    layout, offsets = refine_offsets(binv, layout, cfg)   # 칸 위치 보정은 이전 방식과 같음
    gray = cv2.cvtColor(warped_bgr, cv2.COLOR_BGR2GRAY) if warped_bgr.ndim == 3 else warped_bgr
    r_all = [c["radius_mm"] for q in layout["mcq"]["questions"] for c in q["choices"]] + \
            [b["radius_mm"] for b in layout["student_id"]["bubbles"]]
    half = int(np.ceil(mm_to_px(max(r_all) * 1.5, cfg.dpi))) + 1   # 바깥 링까지 들어가는 크롭 크기
    gp = cv2.copyMakeBorder(gray, half, half, half, half, cv2.BORDER_CONSTANT, value=255)

    # 1. 크롭과 인쇄 마스크 (객관식은 선지 번호별, 학번은 값별로 묶음)
    sid = layout["student_id"]
    mcq_d = [[_crop_darkness(gp, c, half, cfg) for c in q["choices"]] for q in layout["mcq"]["questions"]]
    sid_d = [_crop_darkness(gp, b, half, cfg) for b in sid["bubbles"]]
    groups: dict[Any, list[np.ndarray]] = {}
    for q, ds in zip(layout["mcq"]["questions"], mcq_d):
        for c, d in zip(q["choices"], ds):
            groups.setdefault(("mcq", c["choice"]), []).append(d)
    for b, d in zip(sid["bubbles"], sid_d):
        groups.setdefault(("sid", b["value"]), []).append(d)
    pm = _print_masks(groups, cfg)

    # 2. 과노출 확인: 인쇄 잉크 대비 = 크롭별 잉크 농도 97백분위의 중앙값
    all_d = [d for ds in mcq_d for d in ds] + sid_d
    contrast = float(np.median([np.percentile(d, 97) for d in all_d])) if all_d else 1.0
    # 문항 주변 인쇄 잉크 대비 (부분 반사 확인용): 그 문항 칸들의 97백분위 중앙값
    local_pc = [float(np.median([np.percentile(d, 97) for d in ds])) for ds in mcq_d]
    over = contrast < cfg.expo_min
    retake = cfg.expo_policy == "retake" and contrast < cfg.expo_retake   # 아주 밝음 → 장 단위 재촬영

    # 3. 객관식
    mcq: list[dict[str, Any]] = []
    n_glare = 0
    for q, ds, lpc in zip(layout["mcq"]["questions"], mcq_d, local_pc):
        vals = [_cell_values(d, pm[("mcq", c["choice"])], mm_to_px(c["radius_mm"], cfg.dpi), cfg)
                for c, d in zip(q["choices"], ds)]
        states = [cell_state_darkness(*v, cfg) for v in vals]
        answer, status, reason = decide_group_darkness(states, [c["choice"] for c in q["choices"]], cfg, over)
        # 신뢰도: 이미 검수로 가는 문항은 0, 자동 판정 문항은 기준값까지의 거리
        conf = question_confidence(vals, cfg) if status in ("ok", "blank", "multi") else 0.0
        if status in ("ok", "blank", "multi") and conf < cfg.conf_min:   # 신뢰도 낮음 → 검수
            answer, status, reason = None, "ambiguous", "low_conf"
        # 부분 반사 → 판정과 상관없이 검수. 사진 전체가 밝은(과노출) 사진은 B 방식이 따로 처리하므로 제외
        if cfg.glare_min > 0 and not over and lpc < cfg.glare_min:
            n_glare += 1
            if status != "ambiguous":
                answer, status, reason = None, "ambiguous", "glare"
        mcq.append({"q": q["q"], "answer": answer, "status": status, "needs_review": status == "ambiguous",
                    "reason": reason, "confidence": round(conf, 3), "local_contrast": round(lpc, 3), "fill": [round(v[0], 3) for v in vals], "cells": states,
                    "g": [round(v[0], 3) for v in vals], "cov": [round(v[1], 3) for v in vals],
                    "ring": [round(v[2], 3) for v in vals]})

    # 4. 학번 (자리마다 값 0~9)
    digits: list[dict[str, Any]] = []
    for di in range(sid["digits"]):
        idx = sorted((i for i, b in enumerate(sid["bubbles"]) if b["digit_index"] == di),
                     key=lambda i: sid["bubbles"][i]["value"])
        vals = [_cell_values(sid_d[i], pm[("sid", sid["bubbles"][i]["value"])],
                             mm_to_px(sid["bubbles"][i]["radius_mm"], cfg.dpi), cfg) for i in idx]
        states = [cell_state_darkness(*v, cfg) for v in vals]
        value, status, reason = decide_group_darkness(states, [sid["bubbles"][i]["value"] for i in idx], cfg, over)
        digits.append({"digit_index": di, "value": value, "status": status, "reason": reason,
                       "fill": [round(v[0], 3) for v in vals]})
    text = "".join(str(d["value"][0]) if d["status"] == "ok" and d["value"] else "?" for d in digits)
    statuses = {d["status"] for d in digits}
    sstatus = "ok" if statuses == {"ok"} else ("blank" if statuses == {"blank"} else "review")
    sid_out = {"text": text if sstatus == "ok" else None, "raw": text, "status": sstatus,
               "needs_review": sstatus != "ok", "digits": digits}

    counts: dict[str, int] = {}
    for q in mcq:
        counts[q["status"]] = counts.get(q["status"], 0) + 1
    glare_retake = cfg.expo_policy == "retake" and n_glare > cfg.glare_retake_frac * max(1, len(mcq))
    return {
        "template_id": layout["template_id"],
        "template_version": layout["template_version"],
        "student_id": sid_out,
        "mcq": mcq,
        "summary": {"status_counts": counts,
                    "needs_review": sid_out["needs_review"] or any(q["needs_review"] for q in mcq),
                    "overexposed": over, "print_contrast": round(contrast, 3),
                    "min_confidence": min((q["confidence"] for q in mcq), default=1.0),
                    "glare_questions": n_glare,
                    "retake": retake or glare_retake,   # True면 flow.py가 재촬영 필요로 처리
                    "retake_reason": RETAKE_BRIGHT_MSG if retake else (RETAKE_GLARE_MSG if glare_retake else None)},
        "offsets_mm": offsets,
        "read_config": asdict(cfg),
    }


def read_sheet(warped_bgr: np.ndarray, layout: dict[str, Any], cfg: ReadConfig | None = None) -> dict[str, Any]:
    """P3 메인 함수: 정렬된 답안지 한 장 판독. 결과는 JSON으로 그대로 저장 가능한 dict.
    이진화는 한 장에 한 번만 하고 모든 칸이 같은 이진 이미지를 씀 (속도).

    매개변수:
        warped_bgr: P1 정렬 이미지 (AlignResult.warped, 200dpi 정면 이미지)
        layout: 양식 좌표 dict (layout.json 내용)
        cfg: 판독 설정. None이면 기본값 ReadConfig()
    반환:
        read dict. 키는 아래와 같음
          template_id, template_version : 판독에 쓴 양식 ID·버전 (layout에서 그대로 복사)
          student_id   : read_student_id() 결과
          mcq          : read_mcq() 결과 (문항별 dict 목록)
          summary      : {"status_counts": {"ok": 18, "ambiguous": 2, ...},
                          "needs_review": 학번 또는 문항 중 하나라도 검수 필요하면 True}
          offsets_mm   : 칸 위치 보정량 (refine_offsets() 참고)
          read_config  : 사용한 ReadConfig 값 (dict)
    예외:
        KeyError: layout에 필요한 키가 없을 때
    """
    cfg = cfg or ReadConfig()
    if cfg.method == "darkness":
        return read_sheet_darkness(warped_bgr, layout, cfg)
    binv = binarize(warped_bgr, cfg)
    layout, offsets = refine_offsets(binv, layout, cfg)   # 칸 위치 미세 보정 (인쇄 위치 차이 흡수)
    mcq = read_mcq(binv, layout, cfg)
    sid = read_student_id(binv, layout, cfg)
    counts: dict[str, int] = {}   # 문항 상태별 개수 (예: {"ok": 18, "blank": 1, "ambiguous": 1})
    for q in mcq:
        counts[q["status"]] = counts.get(q["status"], 0) + 1
    return {
        "template_id": layout["template_id"],
        "template_version": layout["template_version"],
        "student_id": sid,
        "mcq": mcq,
        "summary": {"status_counts": counts,
                    "needs_review": sid["needs_review"] or any(q["needs_review"] for q in mcq)},
        "offsets_mm": offsets,          # 칸 위치 보정량 (블록별 [dx, dy] mm)
        "read_config": asdict(cfg),     # 어떤 기준값으로 읽었는지 기록 (실험 재현용)
    }
