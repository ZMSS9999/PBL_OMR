# pyright: basic
"""생성기 좌표 ↔ 팀 좌표 JSON 비교 (팀 양식과 똑같이 찍히는지 확인).

[이 파일의 역할]
  layout.py가 계산한 45문항·5지선다 양식 좌표를 팀 좌표 JSON(tools/team_form/)과 비교해서
  마커, 객관식 동그라미, 서술형 칸이 얼마나 어긋나는지(mm) 출력함
  → 팀 양식이 바뀌면 새 JSON을 tools/team_form/에 덮어쓰고 이 스크립트로 차이를 확인

[입력 → 출력]
  tools/team_form/omr_marks.json(가로), omr_marks_vertical.json(세로) + compute_layout()
  → 터미널에 항목별 최대 차이(mm)와 통과 여부 출력. 0.05mm 넘게 어긋나면 종료 코드 1

[팀 JSON 형식] (PBL 문서/json양식 '답안지 좌표 JSON 설명.pdf')
  - 모든 값은 용지 크기를 1로 둔 비율(*_norm). mm = 비율 × 용지 길이(mm)
  - aruco_markers[]     : aruco_id, corner(TL/TR/BR/BL), left/top/width/height_norm
  - questions[].options[]: option, center_x/y_norm (동그라미 중심), width/height_norm (지름)
  - subjective_answers[] : question(1~5), left/top/width/height_norm (서술형 답 칸)

[사용 예] (backend 폴더에서)
  python tools/check_team_form.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sheetgen import frame as F  # noqa: E402
from sheetgen.layout import SheetSpec, compute_layout  # noqa: E402

TEAM_DIR = os.path.join(os.path.dirname(__file__), "team_form")   # 팀 좌표 JSON 폴더
FILES = {"landscape": "omr_marks.json", "portrait": "omr_marks_vertical.json"}
TOL_MM = 0.05   # 허용 차이 (mm). 팀 JSON 값이 소수 6자리 비율이라 반올림 차이만 허용


def compare(orientation: str) -> float:
    """한 방향의 생성기 좌표와 팀 JSON 좌표를 비교해 항목별 최대 차이를 출력.

    매개변수:
        orientation: "landscape" 또는 "portrait"
    반환:
        전체 항목 중 가장 큰 차이 (mm)
    """
    with open(os.path.join(TEAM_DIR, FILES[orientation]), encoding="utf-8") as f:
        team = json.load(f)
    W, H = F.page_size(orientation)                     # 용지 가로·세로 (mm)
    lay = compute_layout(SheetSpec(num_questions=45, num_choices=5, orientation=orientation))

    # 마커: 왼쪽 위 꼭짓점과 한 변 길이
    mk = {m["id"]: m for m in lay["frame"]["aruco"]["markers"]}
    d_marker = 0.0
    for m in team["aruco_markers"]:
        ours = mk[m["aruco_id"]]
        x, y = ours["corners_mm"][0]
        d_marker = max(d_marker, abs(x - m["left_norm"] * W), abs(y - m["top_norm"] * H),
                       abs(lay["frame"]["aruco"]["marker_size_mm"] - m["width_norm"] * W),
                       abs(ours["pos"] != m["corner"]) * 999)   # 위치 이름이 다르면 크게 표시

    # 객관식 동그라미: 중심과 지름
    qs = {q["q"]: q for q in lay["mcq"]["questions"]}
    d_bubble = 0.0
    for q in team["questions"]:
        for o in q["options"]:
            c = qs[q["question"]]["choices"][o["option"] - 1]
            d_bubble = max(d_bubble, abs(c["center_mm"][0] - o["center_x_norm"] * W),
                           abs(c["center_mm"][1] - o["center_y_norm"] * H),
                           abs(2 * c["radius_mm"] - o["width_norm"] * W))

    # 서술형 칸: 우리 box_bbox_mm (번호 칸을 뺀 답 칸 테두리)
    items = {it["no"]: it for it in lay["short_answers"]["items"]}
    d_sa = 0.0
    for s in team.get("subjective_answers", []):
        x, y, w, h = items[s["question"]]["box_bbox_mm"]
        d_sa = max(d_sa, abs(x - s["left_norm"] * W), abs(y - s["top_norm"] * H),
                   abs(w - s["width_norm"] * W), abs(h - s["height_norm"] * H))

    print(f"{orientation:9} 마커 {d_marker:.3f}mm  객관식 {d_bubble:.3f}mm  서술형 {d_sa:.3f}mm")
    return max(d_marker, d_bubble, d_sa)


if __name__ == "__main__":
    worst = max(compare(o) for o in FILES)
    print("통과" if worst <= TOL_MM else f"차이 큼 (최대 {worst:.3f}mm > {TOL_MM}mm)")
    sys.exit(0 if worst <= TOL_MM else 1)
