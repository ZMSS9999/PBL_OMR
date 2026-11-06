# pyright: basic
"""이중 검증(규칙 기반 + CNN) 설정값.

[이 파일의 역할]
  - CNN 8클래스 이름·번호와 "마킹 / 마킹 아님" 구분을 한곳에 모음
  - 칸 크롭 크기, 기본 가중치 경로

[임계값(60%)은 여기에 두지 않음]
  - 규칙 기반 마킹 기준은 pipeline/read.py의 ReadConfig.marked_min 한 곳에서만 정함
  - dual_verify는 판독 결과에 기록된 read["read_config"]["marked_min"]을 읽어 씀
    → ReadConfig 값만 바꿔도 이중 검증이 자동으로 따라감

[클래스 순서]
  - cnn_learn.py / build_cnn_dataset.py 의 CLASS_NAMES 와 같은 순서여야 함 (학습한 모델의 출력 번호)
"""
from __future__ import annotations

from pathlib import Path

CLASS_NAMES = ["빈칸", "정석 마킹", "얇게 채움", "체크 표시", "동그라미",
               "수정테이프", "번짐", "칸 삐져나옴"]
BLANK, STANDARD, THIN, CHECK, CIRCLE, TAPED, SMUDGE, OVERFLOW = range(8)

# CNN이 이 클래스로 분류하면 "마킹된 칸"
MARKED_CLASSES = frozenset({STANDARD, THIN, CHECK, CIRCLE, SMUDGE, OVERFLOW})
# 이 클래스는 "마킹 아님" (수정테이프로 가린 칸은 마킹이 아님)
NOT_MARKED_CLASSES = frozenset({BLANK, TAPED})

CROP_SIZE = 64        # CNN 입력 한 변 (px). 학습 데이터셋과 같아야 함
CROP_MARGIN = 0.25    # 칸 폭 대비 바깥 여유 비율. 학습 데이터셋과 같아야 함

DEFAULT_WEIGHTS = Path(__file__).resolve().parent / "model" / "best.pt"
