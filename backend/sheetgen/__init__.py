# pyright: basic
"""sheetgen 패키지 — 답안지(OMR 양식) 만들기 담당. 파이프라인의 '준비 단계(답안지 생성)'.

[이 패키지가 하는 일]
  웹·명령행에서 받은 입력값(문항 수, 선지 수, 방향)으로
  1. 칸 좌표(layout)를 계산하고 (layout.py)
  2. 그 좌표대로 인쇄용 PDF를 그림 (render_pdf.py)
  마커·QR 위치처럼 모든 답안지에 공통인 고정 규격은 frame.py에 모아 둠

[파이프라인에서 위치]
  준비(이 패키지) → P1 정렬 → P2 시험 확인 → P3 판독 → P4 채점 → P5 엑셀
  여기서 만든 layout.json이 P1~P3에서 '어디에 무엇이 있는지' 알려 주는 지도 역할을 함

[입력 → 출력]
  SheetSpec ──compute_layout()──► layout(dict) ──render_pdf()──► 답안지 PDF
                                       └──► (app/service.py가) layout.json으로 저장

[파일 구성]
  frame.py      : 방향별 고정 규격 (ArUco 마커 ID·위치, QR 위치, 좌표계, 기준 dpi)
  layout.py     : 입력값 → 칸 좌표 계산, 입력 범위, QR 내용 만들기/해석, mm→px 변환
  render_pdf.py : layout 좌표 → PDF 그리기

[누가 이 패키지를 쓰는지]
  - app/service.py   : SheetSpec, compute_layout, make_qr_payload, render_pdf (답안지 생성·저장)
  - app/main.py      : NUM_QUESTIONS_RANGE, NUM_CHOICES_RANGE, LayoutOverflowError (입력 범위·오류 응답)
  - pipeline/align.py, pipeline/read.py : frame(F), mm_to_px, bbox_to_px, parse_qr_payload
  - tools/smoke_test.py 등 : all_bubbles, bbox_to_px, mm_to_px

[사용 예]
  from sheetgen import SheetSpec, compute_layout, render_pdf
  layout = compute_layout(SheetSpec(num_questions=20, num_choices=5))
  render_pdf(layout, {"exam_id": "EX-...", "qr_payload": "CM1|..."}, "sheet.pdf")
"""
# 아래 import로 `from sheetgen import ...` 형태로 바로 꺼내 쓸 수 있게 공개할 이름을 모아 둠
from .layout import (SheetSpec, compute_layout, make_qr_payload, parse_qr_payload,
                     mm_to_px, bbox_to_px, all_bubbles, LayoutOverflowError,
                     NUM_QUESTIONS_RANGE, NUM_CHOICES_RANGE, TEMPLATE_VERSION)
from .render_pdf import render_pdf
