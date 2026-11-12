# pyright: basic
"""v2: 규칙 기반(60% 이상은 마킹으로 확정) + CNN(60% 미만만 판정, 신뢰도 미만이면 검수).

[판정 방식]
  - 규칙 기반 마킹 기준(기본 60%) 이상인 칸은 규칙 기반이 마킹으로 확정, CNN은 그 미만인 칸만 판정
    (마킹 6종이면 마킹, 빈칸·수정테이프면 마킹 아님) → CNN 클래스 신뢰도가 CONFIDENCE_MIN(기본 0.9) 미만이면 검수
  - 설정값(임계값 등)은 config.py 맨 위 [설정] 부분만 고치면 됨

[규칙 기반 판독(read.py)과 맞추는 부분]
  - 마킹 기준: 잉크 농도 방식(method="darkness", 기본)이면 read_config["solid_min"], g 또는 cov 가 이 값 이상이면 마킹.
              이전 방식("fill")이면 read_config["marked_min"] (fill 은 잉크 농도 방식에서 g 와 같은 값)
  - 팀원 판독이 칸 모양이 아니라 사진 상태 때문에 검수로 보낸 문항·자리(reason 이 glare / overexposed / low_conf)는
    v2가 건드리지 않고 그대로 검수로 둠 (config.KEEP_REVIEW_REASONS). 과노출 재촬영은 flow.py 가 이 앞에서 먼저 처리함
  - 얇게 채움·체크(partial), 동그라미(ring), 흐림(light) 로 보류된 문항·자리는 v2가 CNN으로 다시 판정함 (reason 도 새로 적음)

[파이프라인에서 위치]
  P3 판독(read.py) → [dual_verify_v2.verify_sheet] → P4 채점(grading/score.py)
  연결은 pipeline/flow.py 의 DUAL_VERIFY = "v2" 로 켜고 None 으로 끔 (read 와 같은 형식의 결과를 돌려주므로 뒤 단계는 그대로)
  팀원 코드(pipeline, grading, app)를 import 하지 않음

[입력 → 출력]
  verify_sheet(read, warped, layout)
    read   : read_sheet() 결과 (문항별 fill·cov·reason, 학번 자리별 fill·reason, read_config, offsets_mm 사용)
    warped : P1 정렬 이미지 (200dpi 정면 이미지)
    layout : 양식 좌표 dict (mcq.questions[].choices[], student_id.bubbles[] 의 center_mm, radius_mm 사용)
    → read 의 복사본에 아래를 덧붙여 돌려줌 (기존 키는 지우지 않음)
        객관식  read["mcq"][i]["dual"] : compare.judge_question() 결과 (선지별 판정 출처·CNN 클래스·신뢰도·검수 사유)
                통과 문항   : answer = 마킹된 선지들, status = ok / blank / multi, needs_review = False, reason = ""
                검수 문항   : answer = None, status = "review", needs_review = True, reason = "cnn_low_conf" 또는
                              "cnn_tape_no_mark" (→ score.py 가 "검수 대기"로 처리)
                유지된 문항 : 위 KEEP_REVIEW_REASONS 문항은 값을 안 바꾸고 dual = {"ok": False, "kept": reason}
        학번    read["student_id"]["digits"][i]["dual"] : 위와 같은 형식 (선지 번호 자리에 숫자 0~9)
                검수가 된 자리 : value = None, status = "review" → 학번 text = None, needs_review = True
                학번 text / raw / status / needs_review 는 자리 결과로 다시 계산
        read["summary"]     : needs_review 다시 계산, "dual_verify": {"checked", "review_questions", "review_digits",
                              "kept_questions", "kept_digits"}
        read["dual_verify"] : {"version": "v2", "marked_min": 규칙 기반 마킹 기준, "rule_method", "confidence_min"}

[사용 예]
  import dual_verify_v2
  read = dual_verify_v2.verify_sheet(read, warped, layout)                      # 기본 CNN 사용
  read = dual_verify_v2.verify_sheet(read, warped, layout, classifier=my_fn)    # 테스트: 가짜 분류기
    (my_fn: 칸 이미지 목록 → [(클래스 번호, 신뢰도), ...])
"""
from __future__ import annotations

import copy
import functools
from typing import Any, Callable

import cv2
import numpy as np

from dual_verify_v2 import config
from dual_verify_v2.compare import judge_question, rule_marked
from dual_verify_v2.crop import bubble_polygon, crop_cell

__all__ = ["verify_sheet"]

Classifier = Callable[[list], list]   # 칸 이미지 목록 → [(클래스 번호, 신뢰도), ...]


@functools.lru_cache(maxsize=1)
def _default_classifier() -> Classifier:
    """기본 가중치의 CnnClassifier 를 한 번만 불러와 재사용 (사진마다 다시 불러오지 않게)."""
    from dual_verify_v2.cnn import CnnClassifier    # torch 는 실제로 쓸 때만 불러옴
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


def _crop_bubble(gray: np.ndarray, c: dict[str, Any], dx: float, dy: float, dpi: int) -> np.ndarray:
    """동그라미 칸 하나 → (dx, dy)mm 보정을 반영해 CNN 입력 크기로 자른 이미지."""
    cx = _mm_to_px(c["center_mm"][0] + dx, dpi)
    cy = _mm_to_px(c["center_mm"][1] + dy, dpi)
    r = _mm_to_px(c["radius_mm"], dpi)
    return crop_cell(gray, bubble_polygon(cx, cy, r), config.CROP_SIZE, config.CROP_MARGIN)


def _rule_threshold(rc: dict[str, Any]) -> float:
    """규칙 기반 마킹 기준: 잉크 농도 방식이면 solid_min, 이전 방식이면 marked_min (read_config 에서 읽음)."""
    if rc.get("method") == "darkness" and "solid_min" in rc:
        return float(rc["solid_min"])
    return float(rc["marked_min"])


def _kept_reason(ref: dict[str, Any]) -> str | None:
    """팀원 판독이 사진 상태(반사·과노출·신뢰도)로 검수에 보낸 문항·자리면 그 reason, 아니면 None."""
    if ref.get("status") == "ambiguous" and ref.get("reason") in config.KEEP_REVIEW_REASONS:
        return ref["reason"]
    return None


def _status_of(answer: list[int]) -> str:
    """답 → 상태 (read.py 의 decide_group 과 같은 뜻): 없음 blank / 1개 ok / 2개 이상 multi."""
    return "blank" if not answer else ("ok" if len(answer) == 1 else "multi")


def verify_sheet(read: dict[str, Any], warped: np.ndarray, layout: dict[str, Any],
                 classifier: Classifier | None = None) -> dict[str, Any]:
    """답안지 한 장의 객관식·학번을 v2 방식으로 판정. 원본 read 는 바꾸지 않고 복사본을 돌려줌.

    매개변수:
        read: P3 read_sheet() 결과
        warped: P1 정렬 이미지 (BGR 3채널 또는 흑백)
        layout: 양식 좌표 dict
        classifier: 칸 이미지 목록 → [(클래스 번호, 신뢰도)] 함수. None이면 학습된 CnnClassifier(기본 가중치)를 불러 씀
    반환:
        판정 결과가 반영된 read 복사본 (모듈 설명의 [입력 → 출력] 참고)
    예외:
        FileNotFoundError: classifier 가 None인데 기본 가중치 파일이 없을 때
        ValueError: 분류 결과 수가 칸 수와 다를 때
    """
    if classifier is None:
        classifier = _default_classifier()

    out = copy.deepcopy(read)
    rc = out["read_config"]
    marked_min, dpi = _rule_threshold(rc), int(rc["dpi"])
    conf_min, tape_review = config.CONFIDENCE_MIN, config.TAPE_WITHOUT_MARK_REVIEW   # 호출할 때마다 읽음 (설정 수정 즉시 반영)
    gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY) if warped.ndim == 3 else warped
    offsets = out.get("offsets_mm")

    # 1) 판정 단위(객관식 문항 하나 / 학번 자리 하나)를 모음
    groups: list[dict[str, Any]] = []
    q_by_no = {q["q"]: q for q in layout["mcq"]["questions"]}
    shift = _column_offsets(layout, offsets)
    for rq in out["mcq"]:
        bs = q_by_no[rq["q"]]["choices"]
        groups.append({"kind": "mcq", "ref": rq, "fills": rq["fill"], "covs": rq.get("cov"), "bubbles": bs,
                       "nos": [c["choice"] for c in bs], "shift": shift.get(rq["q"], (0.0, 0.0)),
                       "keep": _kept_reason(rq)})
    digits = out.get("student_id", {}).get("digits") or []
    if config.INCLUDE_STUDENT_ID and digits and layout.get("student_id"):
        sdx, sdy = (offsets or {}).get("student_id") or (0.0, 0.0)
        for d in digits:
            bs = sorted((b for b in layout["student_id"]["bubbles"] if b["digit_index"] == d["digit_index"]),
                        key=lambda b: b["value"])
            groups.append({"kind": "digit", "ref": d, "fills": d["fill"], "covs": None, "bubbles": bs,
                           "nos": [b["value"] for b in bs], "shift": (sdx, sdy), "keep": _kept_reason(d)})

    # 2) 규칙 기반이 마킹으로 확정하지 않은 칸(마킹 기준 미만)만 잘라서 CNN으로 분류. 유지할 문항·자리는 건너뜀
    crops: list[np.ndarray] = []
    slots: list[tuple[int, int]] = []              # crops 와 같은 순서의 (판정 단위 번호, 선지 위치)
    for gi, g in enumerate(groups):
        if g["keep"]:
            continue
        for ci, (b, f) in enumerate(zip(g["bubbles"], g["fills"])):
            if not rule_marked(f, g["covs"][ci] if g["covs"] else None, marked_min):
                crops.append(_crop_bubble(gray, b, g["shift"][0], g["shift"][1], dpi))
                slots.append((gi, ci))
    results = classifier(crops)
    if len(results) != len(crops):
        raise ValueError(f"분류 결과 수({len(results)})가 칸 수({len(crops)})와 다릅니다")
    preds: list[list[Any]] = [[None] * len(g["fills"]) for g in groups]
    for (gi, ci), res in zip(slots, results):
        preds[gi][ci] = res

    # 3) 판정 단위별로 통과 / 검수 결정해서 read 에 반영
    review_questions: list[int] = []
    review_digits: list[int] = []
    kept_questions: list[int] = []
    kept_digits: list[int] = []
    for g, p in zip(groups, preds):
        ref = g["ref"]
        if g["keep"]:                              # 사진 상태 때문에 검수로 간 문항·자리: 값은 그대로 둠
            ref["dual"] = {"ok": False, "kept": g["keep"], "reasons": [{"type": "kept", "reason": g["keep"]}]}
            (kept_questions if g["kind"] == "mcq" else kept_digits).append(ref["q"] if g["kind"] == "mcq" else ref["digit_index"])
            continue
        v = judge_question(g["fills"], p, marked_min, conf_min, g["nos"], tape_review, g["covs"])
        ref["dual"] = v
        # 검수 사유를 reason 에 적음 (통과면 팀원 판독이 남긴 partial·ring·light 같은 보류 사유를 지움)
        why = "" if v["ok"] else ("cnn_tape_no_mark" if v["reasons"][0]["type"] == "tape_no_mark" else "cnn_low_conf")
        if g["kind"] == "mcq":
            if v["ok"]:
                ref["answer"], ref["status"], ref["needs_review"] = v["answer"], _status_of(v["answer"]), False
            else:
                ref["answer"], ref["status"], ref["needs_review"] = None, "review", True
                review_questions.append(ref["q"])
            ref["reason"] = why
        else:
            if v["ok"]:
                ref["value"], ref["status"] = v["answer"], _status_of(v["answer"])
            else:
                ref["value"], ref["status"] = None, "review"
                review_digits.append(ref["digit_index"])
            ref["reason"] = why

    # 4) 학번 text / raw / status 를 자리 결과로 다시 계산 (read.py 의 read_student_id 와 같은 규칙)
    if any(g["kind"] == "digit" for g in groups):
        sid = out["student_id"]
        raw = "".join(str(d["value"][0]) if d["status"] == "ok" and d["value"] else "?" for d in digits)
        statuses = {d["status"] for d in digits}
        status = "ok" if statuses == {"ok"} else "blank" if statuses == {"blank"} else "review"
        sid.update({"raw": raw, "text": raw if status == "ok" else None,
                    "status": status, "needs_review": status != "ok"})

    # 5) 요약 다시 계산
    counts: dict[str, int] = {}
    for rq in out["mcq"]:
        counts[rq["status"]] = counts.get(rq["status"], 0) + 1
    out["summary"]["status_counts"] = counts
    out["summary"]["needs_review"] = (out["student_id"]["needs_review"]
                                      or any(rq["needs_review"] for rq in out["mcq"]))
    out["summary"]["dual_verify"] = {"checked": len(out["mcq"]), "review_questions": review_questions,
                                     "review_digits": review_digits,
                                     "kept_questions": kept_questions, "kept_digits": kept_digits}
    out["dual_verify"] = {"version": "v2", "marked_min": marked_min, "rule_method": rc.get("method", "fill"),
                          "confidence_min": conf_min}
    return out
