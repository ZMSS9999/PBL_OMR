# pyright: basic
"""규칙 기반 판독 + CNN 판독 이중 검증 (객관식만, 학번 칸은 제외).

[이 폴더의 역할]
  - P3 규칙 기반 판독(pipeline/read.py) 결과를 CNN으로 한 번 더 확인해서 마킹 판독의 신뢰도를 높임
  - 규칙 기반과 CNN이 모든 선지에서 일치하면 통과, 하나라도 다르면 그 문항을 검수로 보냄
  - 팀원 코드(pipeline, grading, app)를 import 하지 않음 → 필요한 값은 read / layout dict 에서만 읽음

[파이프라인에서 위치]
  P3 판독(read.py) → [dual_verify.verify_sheet] → P4 채점(grading/score.py)
  연결은 pipeline/flow.py 의 run_photo 한 곳에서만 함

[입력 → 출력]
  verify_sheet(read, warped, layout)
    read   : read_sheet() 결과 (문항별 fill, read_config, offsets_mm 사용)
    warped : P1 정렬 이미지 (200dpi 정면 이미지)
    layout : 양식 좌표 dict (layout["mcq"]["questions"][].choices[] 의 center_mm, radius_mm 사용)
    → read 의 복사본에 아래를 덧붙여 돌려줌 (기존 키는 지우지 않음)
        read["mcq"][i]["dual"]  : {"ok", "rule_marked", "cnn_marked", "cnn", "reasons", "rule_answer"}
        검수가 된 문항       : answer = None, needs_review = True  (→ score.py 가 "검수 대기"로 처리)
        read["summary"]        : needs_review 다시 계산, "dual_verify": {"checked", "review_questions"} 추가
        read["dual_verify"]    : {"marked_min": 사용한 규칙 기반 기준}

[기준값]
  - 규칙 기반 마킹 기준(60%)은 read["read_config"]["marked_min"] 에서 읽음 (여기서 따로 정하지 않음)
  - 팀원 규칙은 25~60% 를 "애매"로 보고 바로 검수로 보내지만, 이중 검증에서는 60% 미만을 모두
    "마킹 아님"으로 보고 CNN이 확인함. 그래서 애매였던 문항도 CNN과 일치하면 통과로 되돌림.

[사용 예]
  import dual_verify
  read = dual_verify.verify_sheet(read, warped, layout)                      # 기본 CNN 사용
  read = dual_verify.verify_sheet(read, warped, layout, classifier=my_fn)    # 테스트: 가짜 분류기
"""
from __future__ import annotations

import copy
import functools
from typing import Any, Callable

import cv2
import numpy as np

from dual_verify.compare import judge_question
from dual_verify.config import CROP_MARGIN, CROP_SIZE
from dual_verify.crop import bubble_polygon, crop_cell

__all__ = ["verify_sheet"]

Classifier = Callable[[list], list]   # 칸 이미지 목록 → 클래스 번호 목록


@functools.lru_cache(maxsize=1)
def _default_classifier() -> Classifier:
    """기본 가중치의 CnnClassifier 를 한 번만 불러와 재사용 (사진마다 다시 불러오지 않게)."""
    from dual_verify.cnn import CnnClassifier    # torch 는 실제로 쓸 때만 불러옴
    return CnnClassifier()


def _mm_to_px(mm: float, dpi: int) -> float:
    return mm * dpi / 25.4


def _column_offsets(layout: dict[str, Any], offsets_mm: dict[str, Any] | None) -> dict[int, tuple[float, float]]:
    """문항 번호 → 칸 위치 보정량 (dx, dy) mm.
    read.py 의 refine_offsets() 와 같은 방식으로 열을 나눔: 첫 선지 x 좌표(0.1mm 단위 반올림)가 같은 문항이 한 열.
    read["offsets_mm"]["mcq"] 는 왼쪽 열부터의 [dx, dy] 목록."""
    shift: dict[int, tuple[float, float]] = {}
    mcq_off = (offsets_mm or {}).get("mcq") or []
    cols: dict[float, list[dict[str, Any]]] = {}
    for q in layout["mcq"]["questions"]:
        cols.setdefault(round(q["choices"][0]["center_mm"][0], 1), []).append(q)
    for i, x in enumerate(sorted(cols)):
        dx, dy = mcq_off[i] if i < len(mcq_off) else (0.0, 0.0)
        for q in cols[x]:
            shift[q["q"]] = (dx, dy)
    return shift


def _rule_answer(fills: list[float], choice_nos: list[int], marked_min: float) -> list[int]:
    """규칙 기반 마킹 기준(marked_min)만으로 만든 답. 예: [0.1, 0.9, 0.0] → [2]"""
    return [no for no, f in zip(choice_nos, fills) if f >= marked_min]


def verify_sheet(read: dict[str, Any], warped: np.ndarray, layout: dict[str, Any],
                 classifier: Classifier | None = None) -> dict[str, Any]:
    """답안지 한 장의 객관식 판독 결과를 CNN으로 이중 검증. 원본 read 는 바꾸지 않고 복사본을 돌려줌.

    매개변수:
        read: P3 read_sheet() 결과
        warped: P1 정렬 이미지 (BGR 3채널 또는 흑백)
        layout: 양식 좌표 dict
        classifier: 칸 이미지 목록 → 클래스 번호 목록 함수. None이면 학습된 CnnClassifier(기본 가중치)를 불러 씀
    반환:
        검증 결과가 덧붙은 read 복사본 (모듈 설명의 [입력 → 출력] 참고)
    예외:
        FileNotFoundError: classifier 가 None인데 기본 가중치 파일이 없을 때
    """
    if classifier is None:
        classifier = _default_classifier()

    out = copy.deepcopy(read)
    cfg = out["read_config"]
    marked_min, dpi = float(cfg["marked_min"]), int(cfg["dpi"])
    gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY) if warped.ndim == 3 else warped
    shift = _column_offsets(layout, out.get("offsets_mm"))

    # 1) 모든 문항·선지의 칸을 잘라 한 번에 분류 (배치로 처리해 빠르게)
    crops: list[np.ndarray] = []
    spans: list[tuple[int, int]] = []             # 문항별 crops 안의 [시작, 끝)
    q_by_no = {q["q"]: q for q in layout["mcq"]["questions"]}
    for rq in out["mcq"]:
        lq = q_by_no[rq["q"]]
        dx, dy = shift.get(rq["q"], (0.0, 0.0))
        start = len(crops)
        for c in lq["choices"]:
            cx = _mm_to_px(c["center_mm"][0] + dx, dpi)
            cy = _mm_to_px(c["center_mm"][1] + dy, dpi)
            r = _mm_to_px(c["radius_mm"], dpi)
            crops.append(crop_cell(gray, bubble_polygon(cx, cy, r), CROP_SIZE, CROP_MARGIN))
        spans.append((start, len(crops)))
    classes = classifier(crops)
    if len(classes) != len(crops):
        raise ValueError(f"분류 결과 수({len(classes)})가 칸 수({len(crops)})와 다릅니다")

    # 2) 문항별 비교 → 통과 / 검수
    review_questions: list[int] = []
    for rq, (a, b) in zip(out["mcq"], spans):
        nos = [c["choice"] for c in q_by_no[rq["q"]]["choices"]]
        verdict = judge_question(rq["fill"], classes[a:b], marked_min, nos)
        rule_ans = _rule_answer(rq["fill"], nos, marked_min)
        verdict["rule_answer"] = rule_ans
        rq["dual"] = verdict
        if verdict["ok"]:
            if rq["status"] == "ambiguous":
                # 팀원 규칙이 애매로 보류했지만 CNN과 일치 → 규칙 기반 기준(marked_min)으로 확정
                rq["answer"] = rule_ans
                rq["status"] = "blank" if not rule_ans else ("ok" if len(rule_ans) == 1 else "multi")
                rq["needs_review"] = False
        else:
            rq["answer"] = None
            rq["needs_review"] = True
            review_questions.append(rq["q"])

    # 3) 요약 다시 계산 (학번 판독의 검수 필요 여부는 그대로 반영)
    counts: dict[str, int] = {}
    for rq in out["mcq"]:
        counts[rq["status"]] = counts.get(rq["status"], 0) + 1
    out["summary"]["status_counts"] = counts
    out["summary"]["needs_review"] = (out["student_id"]["needs_review"]
                                      or any(rq["needs_review"] for rq in out["mcq"]))
    out["summary"]["dual_verify"] = {"checked": len(out["mcq"]), "review_questions": review_questions}
    out["dual_verify"] = {"marked_min": marked_min}
    return out
