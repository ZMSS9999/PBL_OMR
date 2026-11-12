# pyright: basic
"""정렬된 답안지 이미지에서 선지 칸 하나를 CNN 입력(64x64 흑백)으로 자름.

crop_cell()은 build_cnn_dataset.py 의 같은 이름 함수와 같은 방식 (학습 데이터와 같은 입력을 만들기 위함):
  네 꼭짓점(TL, TR, BR, BL)을 원근 보정해 size x size 로 자르고, 칸 폭의 margin 비율만큼 사방으로 넓혀
  칸 밖으로 삐져나온 잉크가 보이게 함.
"""
from __future__ import annotations

import cv2
import numpy as np


def crop_cell(gray: np.ndarray, polygon, size: int, margin: float) -> np.ndarray:
    """polygon(TL,TR,BR,BL, 이미지 좌표)을 원근 보정해 size x size 로 자름.

    매개변수:
        gray: 흑백 이미지 (2차원 uint8)
        polygon: 칸 네 꼭짓점 [(x, y) x 4], 순서 TL, TR, BR, BL
        size: 출력 한 변 (px)
        margin: 칸 폭 대비 바깥 여유 비율 (0.25 = 사방 25%씩 넓힘)
    반환:
        (size, size) uint8 배열
    """
    src = np.asarray(polygon, dtype=np.float32)
    tl, tr, br, bl = src
    u = (tr - tl + br - bl) / 2.0
    v = (bl - tl + br - tr) / 2.0
    c = src.mean(axis=0)
    k = 0.5 + margin
    src = np.stack([c - u * k - v * k, c + u * k - v * k,
                    c + u * k + v * k, c - u * k + v * k]).astype(np.float32)
    dst = np.array([[0, 0], [size - 1, 0], [size - 1, size - 1], [0, size - 1]], dtype=np.float32)
    m = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(gray, m, (size, size), flags=cv2.INTER_CUBIC,
                               borderMode=cv2.BORDER_REPLICATE)


def bubble_polygon(cx_px: float, cy_px: float, r_px: float) -> list[tuple[float, float]]:
    """동그라미 중심·반지름 → 그 동그라미를 감싸는 정사각형 네 꼭짓점 (TL, TR, BR, BL)."""
    return [(cx_px - r_px, cy_px - r_px), (cx_px + r_px, cy_px - r_px),
            (cx_px + r_px, cy_px + r_px), (cx_px - r_px, cy_px + r_px)]
