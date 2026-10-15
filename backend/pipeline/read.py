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

[1단계: 칸 하나 판정]
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
  - read_sheet()     : P3 메인 함수. 답안지 한 장 전체 판독 → read dict

[사용 예]
  from pipeline.read import read_sheet, ReadConfig
  read = read_sheet(res.warped, layout)                         # res = align() 결과
  read["mcq"][0]          # 예: {"q": 1, "answer": [3], "status": "ok", ...}
  read["student_id"]["text"]   # 예: "2026103456" (못 읽으면 None)
  read2 = read_sheet(res.warped, layout, ReadConfig(marked_min=0.5))   # 기준값 바꿔 실험
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import cv2
import numpy as np

from sheetgen import frame as F
from sheetgen.layout import mm_to_px

MARKED, AMBIGUOUS, BLANK = "marked", "ambiguous", "blank"   # 칸 상태 3가지 (마킹 / 애매 / 빈칸)


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
