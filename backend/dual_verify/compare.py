# pyright: basic
"""규칙 기반 판독 결과와 CNN 분류를 비교해 문항별 통과 / 검수를 정함.

[이 파일의 역할]
  - 이미지·모델을 전혀 쓰지 않는 순수 판정 로직 (숫자와 클래스 번호만 받음)
  - CNN 없이도 테스트할 수 있고, 판정 규칙이 바뀌면 이 파일만 고치면 됨

[판정 규칙] 선지마다 규칙 기반 마킹 여부(채움 비율 ≥ marked_min)와 CNN 마킹 여부를 비교
  규칙 마킹   + CNN 마킹(6종)            → 일치
  규칙 마킹   + CNN 빈칸/수정테이프       → 불일치 (검수)
  규칙 미마킹 + CNN 빈칸/수정테이프       → 일치
  규칙 미마킹 + CNN 마킹(6종)            → 불일치 (검수)
  추가 규칙: 수정테이프로 분류된 선지가 있는데 문항에 마킹된 선지가 하나도 없으면 검수
  → 불일치가 하나라도 있거나 추가 규칙에 걸리면 그 문항은 검수

[입력 → 출력]
  judge_question(fills, cnn_classes, marked_min, choice_nos)
    fills       : 선지별 채움 비율 (read["mcq"][i]["fill"])
    cnn_classes : 선지별 CNN 클래스 번호 (config.CLASS_NAMES 순서)
    → {"ok": bool, "rule_marked": [bool], "cnn_marked": [bool], "reasons": [dict]}
"""
from __future__ import annotations

from typing import Any

from dual_verify.config import CLASS_NAMES, MARKED_CLASSES, TAPED

MISMATCH = "mismatch"            # 규칙 기반과 CNN의 마킹 여부가 다름
TAPE_NO_MARK = "tape_no_mark"    # 수정테이프가 있는데 마킹된 선지가 없음


def judge_question(fills: list[float], cnn_classes: list[int], marked_min: float,
                   choice_nos: list[int] | None = None) -> dict[str, Any]:
    """문항 하나의 통과 / 검수 판정.

    매개변수:
        fills: 선지별 채움 비율 (0~1). 예: [0.02, 0.01, 0.91, 0.03, 0.0]
        cnn_classes: 선지별 CNN 클래스 번호. fills와 같은 길이, 같은 순서
        marked_min: 규칙 기반 마킹 기준 (ReadConfig.marked_min)
        choice_nos: 선지 번호 목록 (예: [1, 2, 3, 4, 5]). None이면 1부터 순서대로
    반환:
        {"ok": True/False,                    # True면 통과, False면 검수
         "rule_marked": [False, False, True, ...],   # 규칙 기반 선지별 마킹 여부
         "cnn_marked":  [...],                       # CNN 선지별 마킹 여부
         "cnn": ["빈칸", "정석 마킹", ...],           # CNN 클래스 이름 (검수 화면 참고용)
         "reasons": [{"type": "mismatch", "choice": 3, "rule": True, "cnn": "빈칸"}, ...]}
    예외:
        ValueError: fills, cnn_classes, choice_nos 길이가 다를 때
    """
    if len(fills) != len(cnn_classes):
        raise ValueError(f"선지 수 불일치: fills {len(fills)}개, cnn {len(cnn_classes)}개")
    nos = choice_nos if choice_nos is not None else list(range(1, len(fills) + 1))
    if len(nos) != len(fills):
        raise ValueError(f"선지 수 불일치: choice_nos {len(nos)}개, fills {len(fills)}개")

    rule_marked = [f >= marked_min for f in fills]
    cnn_marked = [c in MARKED_CLASSES for c in cnn_classes]
    reasons: list[dict[str, Any]] = []
    for no, rm, cm, c in zip(nos, rule_marked, cnn_marked, cnn_classes):
        if rm != cm:
            reasons.append({"type": MISMATCH, "choice": no, "rule": rm, "cnn": CLASS_NAMES[c]})
    taped = [no for no, c in zip(nos, cnn_classes) if c == TAPED]
    if taped and not any(rule_marked) and not any(cnn_marked):
        reasons.append({"type": TAPE_NO_MARK, "choices": taped})
    return {"ok": not reasons, "rule_marked": rule_marked, "cnn_marked": cnn_marked,
            "cnn": [CLASS_NAMES[c] for c in cnn_classes], "reasons": reasons}
