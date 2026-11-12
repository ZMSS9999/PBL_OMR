# pyright: basic
"""dual_verify_v2 설정값. ★ 값을 바꿀 때는 이 파일 맨 위 [설정] 부분만 고치면 됨.

[v2 판정 흐름]
  1) 규칙 기반 채움 비율이 marked_min(60%) 이상 → 마킹 (CNN은 보지 않음)
  2) 60% 미만 → CNN이 8클래스로 분류 → 마킹 6종이면 마킹, 빈칸·수정테이프면 마킹 아님
  3) CNN 클래스 신뢰도(예측 클래스의 softmax 확률)가 CONFIDENCE_MIN 미만이면 그 문항은 검수
  ※ 규칙 기반 마킹 기준(60%)은 여기서 정하지 않고 판독 결과의 read["read_config"]에서 읽어 씀
    (pipeline/read.py 의 ReadConfig 한 곳에서만 바꾸면 됨)
      - 잉크 농도 방식(method="darkness", 기본): solid_min. 칸의 g(평균 잉크 농도) 또는 cov(진한 픽셀 비율)가 이 값 이상이면 마킹
      - 이전 방식(method="fill"): marked_min. 채움 비율이 이 값 이상이면 마킹
"""
from __future__ import annotations

from pathlib import Path

# ======================================================================================
# [설정] 여기만 고치면 됨
# ======================================================================================
CONFIDENCE_MIN = 0.9
"""CNN 클래스 신뢰도 임계값 (0~1). 60% 미만 칸에서 CNN이 예측한 클래스의 확률이 이 값 '미만'이면 검수.
   올리면 검수가 늘고(더 확실한 칸만 자동 확정), 내리면 검수가 줄지만 오판독 위험이 커짐."""

TAPE_WITHOUT_MARK_REVIEW = True
"""True: 수정테이프로 분류된 칸이 있는데 그 문항에 마킹된 선지가 하나도 없으면 검수. False: 이 규칙을 쓰지 않음."""

INCLUDE_STUDENT_ID = True
"""True: 학번 칸도 같은 방식으로 판정. False: 객관식만 판정하고 학번은 규칙 기반 결과를 그대로 둠.
   (CNN이 학번 칸 이미지를 학습한 적이 없어서, 학번은 신뢰도 미만으로 검수가 많이 나올 수 있음)"""
# ======================================================================================

CLASS_NAMES = ["빈칸", "정석 마킹", "얇게 채움", "체크 표시", "동그라미",
               "수정테이프", "번짐", "칸 삐져나옴"]       # cnn_learn.py 의 CLASS_NAMES 와 같은 순서
BLANK, STANDARD, THIN, CHECK, CIRCLE, TAPED, SMUDGE, OVERFLOW = range(8)

# CNN이 이 클래스로 분류하면 "마킹된 칸" / 빈칸·수정테이프는 "마킹 아님"
MARKED_CLASSES = frozenset({STANDARD, THIN, CHECK, CIRCLE, SMUDGE, OVERFLOW})
NOT_MARKED_CLASSES = frozenset({BLANK, TAPED})

# 규칙 기반 판독이 칸 모양이 아니라 사진 상태 때문에 검수로 보낸 문항·학번 자리의 reason 값.
# v2는 이 문항·자리를 CNN으로 다시 판정하지 않고 그대로 검수로 둠 (CNN이 바꿔서는 안 되는 안전장치이므로)
#   glare : 문항 주변 부분 반사 / overexposed : 과노출 사진의 무응답 / low_conf : 팀원 문항 신뢰도(conf_min) 미만
# 그 밖의 reason (partial 얇게 채움·체크 / ring 동그라미 / light 흐림 / faint·erasure) 은 v2가 CNN으로 다시 판정함
KEEP_REVIEW_REASONS = frozenset({"glare", "overexposed", "low_conf"})

CROP_SIZE = 64        # CNN 입력 한 변 (px). 학습 데이터셋과 같아야 함
CROP_MARGIN = 0.25    # 칸 폭 대비 바깥 여유 비율. 학습 데이터셋과 같아야 함

DEFAULT_WEIGHTS = Path(__file__).resolve().parent / "model" / "best.pt"
