# pyright: basic
"""합성 데이터셋 가공 도구 — 원본 사진에 흐림·부분 반사를 일부러 넣어 '가공 데이터셋'을 따로 만듦.

[언제 쓰나]
  원본 합성 데이터셋(synthetic_dataset)에 없는 촬영 조건으로 재촬영 기준(E2)을 미리 실험할 때.
    - 흐림: 원본에 흐림 조건이 없어 선명도(blur_var) 재촬영 기준을 정할 수 없었음
    - 부분 반사: 원본 '밝음'은 사진 전체가 밝은 경우뿐. 형광등 반사처럼 일부만 하얗게 날아간 경우를 봐야 함
  원본 폴더는 읽기만 하고 절대 고치지 않음. 결과는 다른 폴더(--out)에 원본과 같은 구조로 저장.

[가공 종류] (--kinds, 기본 둘 다)
  blur  : 초점 흐림(가우시안, sigma 1·2·3·4·6 px) + 손떨림(직선 모션 블러, 길이 8·16·24 px, 방향 무작위)
  glare : 부드러운 타원형 반사 1개. 반지름(사진 너비 대비 0.12 / 0.25) x 세기(0.6 / 0.95) = 4종
          픽셀 = 픽셀 + (255 - 픽셀) x 세기 x 마스크  (마스크는 가운데 1, 가장자리로 갈수록 0인 가우시안)

[원본 고르기]
  조명 normal·dark 사진만 사용 (bright는 이미 밝기 검사 대상이라 섞이면 원인 구분이 안 됨)
  방향마다 --per 장을 일정 간격으로 고름 (기본 80 → 가로 80 + 세로 80 = 160장)

[출력]
  <out>/landscape/<원본이름>_<가공>.jpg + .json , <out>/portrait/... (원본과 같은 구조)
    가공 이름 예: blurg2(가우시안 sigma 2), blurm16(모션 16px), glare_r25_a95(반지름 0.25, 세기 0.95)
  json은 원본 그대로 + condition["augment"] = {"type", 값들, "source": 원본 파일 이름}
    glare는 반사 중심·반지름(px)도 넣음 → 어느 문항이 반사 안에 들었는지 나중에 계산 가능
  <out>/README.txt : 만든 조건과 개수

[실행] (backend 폴더에서)
  python tools/synth_augment.py --src <원본 synthetic_dataset> --out <가공 폴더> [--per 80] [--kinds blur,glare]
  평가: python tools/synth_eval.py --data <가공 폴더> --ref <원본 synthetic_dataset> --out <결과> --shard 0/1

[다른 파일과의 관계]
  - tools/synth_eval.py : 가공 폴더를 --data, 원본 폴더를 --ref로 주면 그대로 평가 (사진별 augment 기록)
"""
from __future__ import annotations

import argparse
import copy
import glob
import json
import os

import cv2
import numpy as np

BLUR_G = [1, 2, 3, 4, 6]          # 가우시안 sigma (px)
BLUR_M = [8, 16, 24]              # 모션 블러 길이 (px)
GLARE = [(0.12, 0.6), (0.12, 0.95), (0.25, 0.6), (0.25, 0.95)]   # (반지름 비율, 세기)


def pick_sources(src: str, orientation: str, per: int) -> list[str]:
    """원본 json 중 조명 normal·dark만 골라 일정 간격으로 per장 선택.

    매개변수:
        src: 원본 데이터셋 폴더
        orientation: "landscape" / "portrait"
        per: 고를 장 수
    반환:
        원본 json 경로 목록
    """
    files = sorted(glob.glob(os.path.join(src, orientation, "*.json")))
    ok = []
    for f in files:
        d = json.load(open(f, encoding="utf-8"))
        if d["condition"]["lighting"] in ("normal", "dark") and os.path.exists(f[:-5] + ".jpg"):
            ok.append(f)
    step = max(1, len(ok) // per)
    return ok[::step][:per]


def motion_kernel(length: int, angle_deg: float) -> np.ndarray:
    """길이 length px, 각도 angle_deg의 직선 모션 블러 커널 (합 1).

    매개변수:
        length: 흔들린 길이 (px)
        angle_deg: 흔들린 방향 (도)
    반환:
        length x length float32 커널
    """
    k = np.zeros((length, length), np.float32)
    k[length // 2, :] = 1.0
    M = cv2.getRotationMatrix2D((length / 2 - 0.5, length / 2 - 0.5), angle_deg, 1.0)
    k = cv2.warpAffine(k, M, (length, length))
    return k / max(k.sum(), 1e-6)


def add_glare(img: np.ndarray, center: tuple[float, float], radius: float, alpha: float) -> np.ndarray:
    """부드러운 원형 반사 1개를 더함. 가운데일수록 흰색(255)에 가까워짐.

    매개변수:
        img: BGR 사진
        center: 반사 중심 (x, y) px
        radius: 반사 반지름 (px, 가우시안 시그마 = radius / 2)
        alpha: 세기 0~1 (1이면 가운데가 완전히 흰색)
    반환:
        가공한 사진 (uint8)
    """
    h, w = img.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d2 = (xx - center[0]) ** 2 + (yy - center[1]) ** 2
    mask = np.exp(-d2 / (2 * (radius / 2) ** 2)) * alpha
    out = img.astype(np.float32)
    out += (255 - out) * mask[..., None]
    return np.clip(out, 0, 255).astype(np.uint8)


def sheet_center_box(d: dict) -> tuple[float, float, float, float]:
    """답안 칸이 있는 범위 (사진 px, x0 y0 x1 y1). 반사 중심을 이 안에서 고름.

    매개변수:
        d: 원본 라벨 json 내용
    반환:
        (x0, y0, x1, y1)
    """
    pts = np.array([p for q in d["questions"] for op in q["options"] for p in op["polygon_px"]], np.float32)
    return float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max())


def save(out_dir: str, orientation: str, base: str, tag: str, img: np.ndarray, d: dict,
         aug: dict, quality: int) -> None:
    """가공 사진과 json 저장 (원본 json 복사 + condition["augment"]).

    매개변수:
        out_dir, orientation: 출력 폴더와 방향
        base: 원본 파일 이름 (확장자 없음)
        tag: 가공 이름 (예: "blurg2")
        img: 가공한 사진
        d: 원본 json 내용
        aug: 가공 내용 dict
        quality: JPEG 품질
    반환:
        없음
    """
    name = f"{base}_{tag}"
    nd = copy.deepcopy(d)
    nd["image"] = name + ".jpg"
    nd["condition"]["augment"] = aug | {"source": base + ".jpg"}
    cv2.imwrite(os.path.join(out_dir, orientation, name + ".jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    with open(os.path.join(out_dir, orientation, name + ".json"), "w", encoding="utf-8") as f:
        json.dump(nd, f, ensure_ascii=False)


def main() -> None:
    """명령행 옵션을 읽어 가공 데이터셋 생성."""
    ap = argparse.ArgumentParser(description="합성 데이터셋 가공 (흐림·부분 반사)")
    ap.add_argument("--src", required=True, help="원본 synthetic_dataset 폴더 (읽기만 함)")
    ap.add_argument("--out", required=True, help="가공 데이터셋 폴더 (원본과 다른 곳)")
    ap.add_argument("--per", type=int, default=80, help="방향별 원본 장 수 (기본 80)")
    ap.add_argument("--kinds", default="blur,glare", help="가공 종류 (blur, glare 중 쉼표로)")
    ap.add_argument("--part", default="", help="'i/n' 이면 원본 목록의 i번째 조각만 처리 (오래 못 도는 환경용)")
    a = ap.parse_args()
    if os.path.abspath(a.src) == os.path.abspath(a.out):
        raise SystemExit("--out은 원본과 다른 폴더여야 함")
    kinds = set(a.kinds.split(","))
    n = 0
    for o in ("landscape", "portrait"):
        os.makedirs(os.path.join(a.out, o), exist_ok=True)
        srcs = pick_sources(a.src, o, a.per)
        if a.part:
            i, k = map(int, a.part.split("/"))
            srcs = srcs[i::k]
        for f in srcs:
            d = json.load(open(f, encoding="utf-8"))
            img = cv2.imread(f[:-5] + ".jpg")
            base = os.path.basename(f)[:-5]
            q = int(d.get("jpeg_quality", 90))
            # 같은 원본이면 몇 번 돌려도 같은 결과가 나오도록 원본 이름으로 난수 고정
            r = np.random.default_rng(int.from_bytes((base + o).encode(), "little") % (2 ** 32))
            if "blur" in kinds:
                for s in BLUR_G:
                    save(a.out, o, base, f"blurg{s}", cv2.GaussianBlur(img, (0, 0), s), d,
                         {"type": "blur_gauss", "sigma": s}, q)
                    n += 1
                for L in BLUR_M:
                    ang = float(r.uniform(0, 180))
                    save(a.out, o, base, f"blurm{L}", cv2.filter2D(img, -1, motion_kernel(L, ang)), d,
                         {"type": "blur_motion", "length": L, "angle": round(ang, 1)}, q)
                    n += 1
            if "glare" in kinds:
                x0, y0, x1, y1 = sheet_center_box(d)
                for rr, al in GLARE:
                    c = (float(r.uniform(x0, x1)), float(r.uniform(y0, y1)))
                    rad = rr * img.shape[1]
                    save(a.out, o, base, f"glare_r{int(rr * 100)}_a{int(al * 100)}", add_glare(img, c, rad, al), d,
                         {"type": "glare", "radius_ratio": rr, "alpha": al,
                          "center_px": [round(c[0], 1), round(c[1], 1)], "radius_px": round(rad, 1)}, q)
                    n += 1
    with open(os.path.join(a.out, "README.txt"), "a", encoding="utf-8") as f:
        f.write(f"원본: {a.src}\n가공: {sorted(kinds)}  방향별 원본 {a.per}장 (조명 normal·dark)  part={a.part or '전체'}  만든 사진 {n}장\n"
                f"  흐림 가우시안 sigma {BLUR_G}, 모션 길이 {BLUR_M}\n  반사 (반지름 비율, 세기) {GLARE}\n"
                "원본 폴더는 고치지 않음. 만든 도구: checkmate/backend/tools/synth_augment.py\n")
    print(f"완료: {n}장 → {a.out}")


if __name__ == "__main__":
    main()
