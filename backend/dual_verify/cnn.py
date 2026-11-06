# pyright: basic
"""CNN 분류기: 칸 이미지(64x64 흑백) 여러 장 → 클래스 번호 목록.

학습된 best.pt 를 불러 쓰며, 파일에 저장된 arch / pool / dropout 으로 모델 구조를 복원함.
torch 는 이 파일을 실제로 쓸 때만 불러옴 (dual_verify 를 import 하는 것만으로는 torch 가 필요 없음).

[사용 예]
  clf = CnnClassifier()                 # 기본 가중치 dual_verify/model/best.pt
  classes = clf(crops)                  # crops: 64x64 uint8 배열 목록 → [0, 1, 5, ...]
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from dual_verify.config import CROP_SIZE, DEFAULT_WEIGHTS


class CnnClassifier:
    """best.pt 를 불러 칸 이미지를 8클래스로 분류. 호출 가능한 객체 (classifier(crops) → 클래스 번호 목록)."""

    def __init__(self, weights_path: str | Path | None = None, device: str | None = None,
                 batch_size: int = 512):
        import torch
        from dual_verify.model import SmallCNN

        path = Path(weights_path) if weights_path else DEFAULT_WEIGHTS
        if not path.is_file():
            raise FileNotFoundError(f"CNN 가중치 파일이 없습니다: {path}")
        self._torch = torch
        self.device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
        self.batch_size = batch_size
        ckpt = torch.load(path, map_location=self.device, weights_only=True)
        self.model = SmallCNN(ckpt["arch"], ckpt["pool"], ckpt.get("dropout", 0.3)).to(self.device)
        self.model.load_state_dict(ckpt["model"])
        self.model.eval()

    def __call__(self, crops: list[np.ndarray]) -> list[int]:
        """crops: (CROP_SIZE, CROP_SIZE) uint8 배열 목록 → 같은 순서의 클래스 번호 목록."""
        torch = self._torch
        if not crops:
            return []
        x = np.stack(crops)
        if x.shape[1:] != (CROP_SIZE, CROP_SIZE):
            raise ValueError(f"칸 이미지 크기가 {CROP_SIZE}x{CROP_SIZE}이 아님: {x.shape[1:]}")
        out: list[int] = []
        with torch.no_grad():
            for i in range(0, len(x), self.batch_size):
                xb = torch.from_numpy(x[i:i + self.batch_size]).unsqueeze(1).to(self.device).float() / 255.0
                out.extend(self.model(xb).argmax(1).tolist())
        return out
