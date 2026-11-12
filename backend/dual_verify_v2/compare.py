# pyright: basic
"""v2 판정 로직: 선지별 마킹 여부를 정하고 문항 하나를 통과 / 검수로 나눔.

[이 파일의 역할]
  - 이미지·모델을 쓰지 않는 순수 판정 로직 (숫자와 (클래스, 신뢰도)만 받음) → CNN 없이 테스트 가능

[판정 규칙] 선지마다
  채움 비율(또는 cov) ≥ marked_min                 → 마킹 (규칙 기반이 확정, CNN은 보지 않음)
  그 미만 + CNN 신뢰도 ≥ conf_min                  → CNN 클래스가 마킹 6종이면 마킹, 빈칸·수정테이프면 마킹 아님
  그 미만 + CNN 신뢰도 <  conf_min                 → 확신 부족 → 문항 검수
  추가 규칙(tape_review=True): 수정테이프로 (확신 있게) 분류된 칸이 있는데 마킹된 선지가 없으면 문항 검수
  → 검수 사유가 하나라도 있으면 그 문항은 검수, 없으면 통과 (답 = 마킹된 선지들)

[입력 → 출력]
  judge_question(fills, preds, marked_min, conf_min, nos, tape_review)
    fills : 선지별 채움 비율 (잉크 농도 방식이면 g). covs 를 주면 cov 도 같이 봄
    preds : 선지별 CNN 결과 [(클래스 번호, 신뢰도) 또는 None]. 규칙 기반이 마킹으로 확정한 선지는 None 이어도 됨
    → {"ok", "answer", "marked", "source", "cnn", "confidence", "reasons"}
"""
from __future__ import annotations

from typing import Any

from dual_verify_v2.config import CLASS_NAMES, MARKED_CLASSES, TAPED

LOW_CONFIDENCE = "low_confidence"    # CNN 신뢰도가 conf_min 미만
TAPE_NO_MARK = "tape_no_mark"        # 수정테이프가 있는데 마킹된 선지가 없음


def rule_marked(fill: float, cov: float | None, marked_min: float) -> bool:
    """규칙 기반이 이 칸을 마킹으로 확정하는가. 채움(g) 또는 cov 가 marked_min 이상이면 True (cov 가 없으면 채움만 봄)."""
    return fill >= marked_min or (cov is not None and cov >= marked_min)


def judge_question(fills: list[float], preds: list[tuple[int, float] | None], marked_min: float,
                   conf_min: float, nos: list[int] | None = None, tape_review: bool = True,
                   covs: list[float] | None = None) -> dict[str, Any]:
    """문항 하나의 통과 / 검수 판정.

    매개변수:
        fills: 선지별 채움 비율 (0~1). 예: [0.02, 0.01, 0.91, 0.03, 0.0]
               (잉크 농도 방식에서는 칸의 평균 잉크 농도 g, 이전 방식에서는 검은 픽셀 비율)
        preds: 선지별 CNN 결과 [(클래스 번호, 클래스 신뢰도) / None]. fills와 같은 길이, 같은 순서
               규칙 기반이 마킹으로 확정하지 않은 선지는 반드시 값이 있어야 함
        marked_min: 규칙 기반 마킹 기준 (잉크 농도 방식이면 ReadConfig.solid_min, 이전 방식이면 marked_min)
        conf_min: CNN 클래스 신뢰도 임계값 (config.CONFIDENCE_MIN)
        nos: 선지 번호 목록 (예: [1, 2, 3, 4, 5], 학번이면 0~9). None이면 1부터 순서대로
        tape_review: True면 수정테이프 + 마킹 없음 규칙을 적용
        covs: 선지별 진한 픽셀 비율 (잉크 농도 방식의 cov). 주면 fills 또는 covs 가 marked_min 이상인 선지를
              규칙 기반 마킹으로 봄 (read.py 의 cell_state_darkness 와 같은 기준). 없으면 fills 만 봄
    반환:
        {"ok": True/False,                  # True면 통과, False면 검수
         "answer": [3],                     # 마킹된 선지 번호 (통과일 때 답으로 씀)
         "marked": [False, False, True, ...],   # 선지별 마킹 여부
         "source": ["cnn", "cnn", "rule", ...], # 선지별 판정 출처: rule(채움 기준) / cnn / low_conf(확신 부족)
         "cnn": [None, "빈칸", ...],             # CNN 클래스 이름 (규칙으로 확정한 선지는 None)
         "confidence": [None, 0.9987, ...],     # CNN 클래스 신뢰도
         "reasons": [{"type": "low_confidence", "choice": 4, "cnn": "얇게 채움", "confidence": 0.83}, ...]}
    예외:
        ValueError: 길이가 서로 다르거나, 채움이 marked_min 미만인 선지에 CNN 결과가 없을 때
    """
    if len(fills) != len(preds):
        raise ValueError(f"선지 수 불일치: fills {len(fills)}개, preds {len(preds)}개")
    nos = nos if nos is not None else list(range(1, len(fills) + 1))
    if len(nos) != len(fills):
        raise ValueError(f"선지 수 불일치: nos {len(nos)}개, fills {len(fills)}개")

    marked: list[bool] = []
    source: list[str] = []
    names: list[str | None] = []
    confs: list[float | None] = []
    reasons: list[dict[str, Any]] = []
    taped: list[int] = []
    for i, (no, f, p) in enumerate(zip(nos, fills, preds)):
        if rule_marked(f, covs[i] if covs else None, marked_min):    # 1단계: 규칙 기반이 마킹으로 확정 (CNN 안 봄)
            marked.append(True); source.append("rule"); names.append(None); confs.append(None)
            continue
        if p is None:
            raise ValueError(f"{no}번 선지는 채움 {f}로 {marked_min} 미만인데 CNN 결과가 없습니다")
        cls, conf = p                             # 2단계: 60% 미만은 CNN이 판정
        names.append(CLASS_NAMES[cls]); confs.append(round(float(conf), 4))
        if conf < conf_min:                       # 신뢰도 부족 → 검수
            marked.append(False); source.append("low_conf")
            reasons.append({"type": LOW_CONFIDENCE, "choice": no, "cnn": CLASS_NAMES[cls],
                            "confidence": round(float(conf), 4)})
            continue
        marked.append(cls in MARKED_CLASSES); source.append("cnn")
        if cls == TAPED:
            taped.append(no)
    if tape_review and taped and not any(marked):
        reasons.append({"type": TAPE_NO_MARK, "choices": taped})
    return {"ok": not reasons, "answer": [no for no, m in zip(nos, marked) if m], "marked": marked,
            "source": source, "cnn": names, "confidence": confs, "reasons": reasons}
