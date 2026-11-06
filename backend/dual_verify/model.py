# pyright: basic
"""SmallCNN 모델 구조 (추론용).

cnn_learn.py 의 SmallCNN 과 같은 구조여야 학습한 best.pt 가중치를 그대로 불러올 수 있음.
(레이어 이름·순서를 바꾸면 state_dict 가 맞지 않음. 구조를 바꾸려면 cnn_learn.py 와 함께 바꿀 것)

입력: (B, 1, 64, 64) 흑백, 값 0~1 (정규화는 모델 안에서 함) / 출력: (B, 8) 클래스 점수
"""
from __future__ import annotations

import torch.nn as nn

NUM_CLASSES = 8


def conv_block(cin: int, cout: int) -> nn.Sequential:
    return nn.Sequential(
        nn.Conv2d(cin, cout, 3, padding=1, bias=False),
        nn.BatchNorm2d(cout),
        nn.ReLU(inplace=True),
        nn.MaxPool2d(2),
    )


class SmallCNN(nn.Module):
    """Conv-BN-ReLU-MaxPool 블록 4개 → Global Average Pooling → Dropout → FC(8)."""
    ARCH = {"base": [32, 64, 128, 256]}

    def __init__(self, arch: str = "base", pool: str = "gap", dropout: float = 0.3):
        super().__init__()
        layers, cin = [], 1
        for c in self.ARCH[arch]:
            layers.append(conv_block(cin, c))
            cin = c
        self.features = nn.Sequential(*layers)
        self.pool = nn.AdaptiveAvgPool2d(1 if pool == "gap" else 2)
        self.drop = nn.Dropout(dropout)
        self.fc = nn.Linear(cin * (1 if pool == "gap" else 4), NUM_CLASSES)

    def forward(self, x):
        x = (x - 0.5) / 0.5
        x = self.pool(self.features(x)).flatten(1)
        return self.fc(self.drop(x))
