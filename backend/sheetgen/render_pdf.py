# pyright: basic
"""양식(layout) + 시험 정보(exam) → 인쇄용 A4 PDF.

[이 파일의 역할]
  layout.py가 계산한 칸 좌표를 받아서 학생에게 나눠 줄 답안지 PDF를 그림.
  ArUco 마커, QR 코드, 이름·학번 칸, 객관식 동그라미, 주관식 칸, 바닥글을 그림

[파이프라인에서 위치]
  준비 단계(답안지 생성)의 마지막 순서. 여기서 나온 PDF를 인쇄 → 학생이 마킹 → 사진 촬영 →
  P1 정렬 → P2 시험 확인 → P3 판독 → P4 채점 → P5 엑셀

[입력 → 출력]
  layout(dict, layout.py 출력) + exam(dict: exam_id, qr_payload 등) ──render_pdf()──► PDF 파일

[다른 파일과의 관계]
  - 이 파일을 부르는 곳: app/service.py의 create_answer_sheet() (render_pdf(layout, record, pdf_path))
  - 이 파일이 부르는 것: sheetgen/frame.py (ARUCO_DICT_NAME), reportlab(PDF 그리기),
    OpenCV(cv2: ArUco 마커 비트 패턴·QR 코드 패턴 만들기)
  - layout.py는 import하지 않음. 이미 계산된 layout dict만 받음

[핵심 원칙]
  - 여기서는 좌표 계산을 하지 않음. layout에 적힌 좌표만 그대로 그림
  - PDF와 layout.json이 같은 계산 결과에서 나옴 → 인쇄물과 판독 좌표가 어긋날 수 없음

[좌표 변환]
  - 우리 좌표: mm, 왼쪽 위 원점, y 아래로 증가
  - reportlab : pt(1mm = 2.835pt), 왼쪽 아래 원점, y 위로 증가
  → _Pen 클래스가 y_pt = (용지 높이 − y_mm) × mm 로 뒤집어서 그림

[주요 함수·클래스]
  register_font() : 한글 글꼴 찾아서 reportlab에 등록
  _Pen            : mm·왼쪽 위 원점 좌표로 사각형·원·선·글자를 그리게 해 주는 도우미 클래스
  _draw_aruco()   : ArUco 마커 1개를 벡터 사각형으로 그림
  _draw_qr()      : QR 코드를 벡터 사각형으로 그림
  render_pdf()    : 메인 함수. 답안지 한 장을 PDF 파일로 저장

[사용 예]
  layout = compute_layout(SheetSpec(num_questions=20, num_choices=5))
  exam = {"exam_id": "EX-20261001-8C7353", "exam_name": "중간고사", "subject": "컴퓨터비전",
          "qr_payload": make_qr_payload("EX-20261001-8C7353", "P20Q5C", "1", "컴퓨터비전", "중간고사")}
  render_pdf(layout, exam, "answer_sheet.pdf")
"""
from __future__ import annotations

import os

import cv2
from reportlab.lib.colors import Color, black, white
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from . import frame as F

# 색상 값은 (R, G, B) 각각 0~1 비율 (0 = 검정, 1 = 흰색)
GRAY = Color(0.45, 0.45, 0.45)          # 동그라미 안 숫자·안내문 (옅게: 판독 때 잉크로 덜 잡히게)
LINE = Color(0.6, 0.6, 0.6)             # 객관식 행 구분선
HEADER_FILL = Color(0.94, 0.94, 0.94)   # 영역 머리글 배경 (팀 양식 #F0F0F0)

# reportlab은 TrueType(.ttf) 글꼴만 임베드 가능 (.otf/.ttc CFF 글꼴은 불가)
# 아래 목록을 앞에서부터 확인해서 처음 찾은 파일을 씀 (register_font 참고)
FONT_CANDIDATES = [
    os.path.join(os.path.dirname(__file__), "..", "fonts", "NotoSansKR-subset.ttf"),  # 저장소 동봉 (OFL)
    "C:/Windows/Fonts/malgun.ttf",                                                   # 윈도우 맑은 고딕
]


def register_font(font_path: str | None = None) -> str:
    """한글 글꼴 등록. 우선순위: 인자 → 환경변수 CHECKMATE_FONT → 동봉 글꼴 → 맑은 고딕 → 내장 CID 글꼴.

    후보 중 실제로 존재하는 첫 파일을 "KFont"라는 이름으로 등록함.
    "KFont"가 이미 등록돼 있으면 다시 등록하지 않음 (처음 등록한 파일이 계속 쓰임).
    TTF 파일을 하나도 못 찾으면 reportlab 내장 CID 글꼴(HYGothic-Medium)을 씀
    → 이 경우 글꼴이 PDF에 들어가지 않아서 보는 컴퓨터에 따라 글자가 다르게 보일 수 있음.

    매개변수:
        font_path: 직접 지정할 .ttf 글꼴 경로 (없으면 None)
    반환:
        reportlab에서 쓸 글꼴 이름 문자열 ("KFont" 또는 "HYGothic-Medium")
    """
    for p in [font_path, os.environ.get("CHECKMATE_FONT")] + FONT_CANDIDATES:
        if p and os.path.exists(p):
            if "KFont" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("KFont", p))
            return "KFont"
    pdfmetrics.registerFont(UnicodeCIDFont("HYGothic-Medium"))   # 최후 대체 (임베드 안 됨)
    return "HYGothic-Medium"


class _Pen:
    """mm·좌상단 원점 좌표를 reportlab(pt·좌하단 원점)으로 바꿔 그리는 래퍼.
    그리는 쪽 코드는 layout의 mm 값을 그대로 넘기기만 하면 됨.

    속성:
        c: reportlab 캔버스 (실제로 PDF에 그리는 객체)
        font: 글자에 쓸 글꼴 이름 (register_font() 반환값)
        H: 용지 세로 길이 (mm). y축을 뒤집을 때 씀

    참고: 아래 메서드의 lw(선 두께)는 pt 단위 (1pt ≈ 0.353mm), 좌표·길이는 모두 mm 단위.
    """

    def __init__(self, c: canvas.Canvas, font: str, page_h_mm: float):
        """펜 만들기.

        매개변수:
            c: 그릴 reportlab 캔버스
            font: 글꼴 이름 (예: "KFont")
            page_h_mm: 용지 세로 길이 (mm, 세로 A4 = 297.0, 가로 A4 = 210.0)
        """
        self.c, self.font, self.H = c, font, page_h_mm

    def y(self, y_mm):
        """우리 y(mm, 위→아래) → reportlab y(pt, 아래→위).

        매개변수:
            y_mm: 용지 위쪽에서 잰 y 좌표 (mm)
        반환:
            용지 아래쪽에서 잰 y 좌표 (pt). 예: 세로 A4에서 y_mm=0 → 297mm 위치 = 약 841.9pt
        """
        return (self.H - y_mm) * mm

    def rect(self, bbox, fill: Color | None = None, stroke: Color | None = black, lw=0.6):
        """[x, y, w, h] mm 사각형. fill=None이면 속 비움, stroke=None이면 테두리 없음.

        매개변수:
            bbox: 사각형 [x, y, w, h] (mm, x·y는 왼쪽 위 꼭짓점)
            fill: 채울 색 (None이면 채우지 않음)
            stroke: 테두리 색 (기본 검정, None이면 테두리 없음)
            lw: 테두리 선 두께 (pt, 기본 0.6)
        """
        x, y, w, h = bbox
        self.c.setLineWidth(lw)
        if stroke is not None:
            self.c.setStrokeColor(stroke)
        if fill is not None:
            self.c.setFillColor(fill)
        # reportlab의 rect는 '왼쪽 아래' 꼭짓점 기준이라 y 대신 아래 변(y + h)을 뒤집어서 넘김
        self.c.rect(x * mm, self.y(y + h), w * mm, h * mm,
                    fill=int(fill is not None), stroke=int(stroke is not None))

    def circle(self, cx, cy, r, lw=0.6):
        """검은 테두리 빈 원 그리기 (마킹 동그라미용).

        매개변수:
            cx, cy: 원 중심 (mm)
            r: 반지름 (mm)
            lw: 선 두께 (pt, 기본 0.6)
        """
        self.c.setLineWidth(lw)
        self.c.setStrokeColor(black)
        self.c.circle(cx * mm, self.y(cy), r * mm, stroke=1, fill=0)

    def hline(self, x0, x1, y, lw=0.3, color=LINE):
        """가로선 그리기 (객관식 행 구분선 등).

        매개변수:
            x0, x1: 선의 왼쪽 끝·오른쪽 끝 x (mm)
            y: 선의 y 위치 (mm)
            lw: 선 두께 (pt, 기본 0.3)
            color: 선 색 (기본 LINE 회색)
        """
        self.c.setLineWidth(lw)
        self.c.setStrokeColor(color)
        self.c.line(x0 * mm, self.y(y), x1 * mm, self.y(y))

    def vline(self, x, y0, y1, lw=0.3, color=LINE):
        """세로선 그리기 (객관식 번호 칸 오른쪽 구분선 등).

        매개변수:
            x: 선의 x 위치 (mm)
            y0, y1: 선의 위쪽 끝·아래쪽 끝 y (mm)
            lw: 선 두께 (pt, 기본 0.3)
            color: 선 색 (기본 LINE 회색)
        """
        self.c.setLineWidth(lw)
        self.c.setStrokeColor(color)
        self.c.line(x * mm, self.y(y0), x * mm, self.y(y1))

    def text(self, x, y_baseline, s, size: float = 9, align="left", color: Color = black):
        """글자 한 줄 쓰기.

        매개변수:
            x: 기준 x (mm). align이 "left"면 글자 왼쪽 끝, "center"면 가운데, "right"면 오른쪽 끝
            y_baseline: 글자 밑줄(baseline) y (mm)
            s: 쓸 문자열
            size: 글자 크기 (pt, 기본 9)
            align: 정렬 ("left" / "center" / "right")
            color: 글자 색 (기본 검정)
        예외:
            KeyError: align이 세 값 중 하나가 아닐 때
        """
        self.c.setFont(self.font, size)
        self.c.setFillColor(color)
        # align 값에 맞는 reportlab 그리기 함수를 골라서 바로 호출
        {"left": self.c.drawString, "center": self.c.drawCentredString,
         "right": self.c.drawRightString}[align](x * mm, self.y(y_baseline), s)

    def text_in(self, bbox, s, size: float = 9, align="center", color: Color = black, pad=2.0):
        """bbox 안 세로 가운데에 글자 쓰기.

        매개변수:
            bbox: 글자를 넣을 칸 [x, y, w, h] (mm)
            s: 쓸 문자열
            size: 글자 크기 (pt, 기본 9)
            align: 가로 정렬 ("left" / "center" / "right", 기본 가운데)
            color: 글자 색 (기본 검정)
            pad: 왼쪽·오른쪽 정렬일 때 칸 테두리에서 띄우는 거리 (mm, 기본 2.0)
        """
        x, y, w, h = bbox
        # 0.352 = 1pt를 mm로 바꾸는 값. 글자 크기의 약 35%만큼 baseline을 내려서 눈으로 보기에 가운데로 맞춤
        base = y + h / 2 + size * 0.352 * 0.35      # pt → mm, 대략 세로 가운데
        tx = {"left": x + pad, "center": x + w / 2, "right": x + w - pad}[align]
        self.text(tx, base, s, size, align, color)


def _draw_aruco(pen: _Pen, marker_id: int, x, y, size):
    """ArUco 마커를 PDF 벡터 사각형으로 그림 (이미지로 붙이지 않음 → 어떤 배율로 인쇄해도 경계가 선명).
    OpenCV로 8x8 비트 배열(6x6 패턴 + 테두리)을 얻고, 검은 바탕 위에 흰 칸만 덧그림.

    매개변수:
        pen: 그릴 _Pen
        marker_id: 마커 ID (0~3, 세로·가로 공통)
        x, y: 마커 왼쪽 위 꼭짓점 (mm)
        size: 마커 한 변 길이 (mm, 보통 frame.MARKER_SIZE_MM = 18.249)
    """
    d = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, F.ARUCO_DICT_NAME))
    bits = d.markerSize + 2                      # 6x6 + 검은 테두리 1칸씩
    # 1비트 = 1px인 작은 마커 이미지 (bits x bits). 값 255 = 흰 칸, 0 = 검은 칸
    img = cv2.aruco.generateImageMarker(d, marker_id, bits, borderBits=1)
    cell = size / bits                           # 비트 한 칸의 한 변 길이 (mm)
    pen.rect([x, y, size, size], fill=black, stroke=None)
    # 흰 칸만 골라서 검은 바탕 위에 덧그림
    for r in range(bits):
        for c in range(bits):
            if img[r, c] > 127:
                pen.rect([x + c * cell, y + r * cell, cell, cell], fill=white, stroke=None)


def _draw_qr(pen: _Pen, payload: str, bbox):
    """QR도 마커처럼 모듈(칸) 하나씩 벡터 사각형으로 그림. 별도 QR 라이브러리 없이 OpenCV 인코더 사용.
    오류 정정 수준 M(약 15% 손상까지 복원). 둘레에 2칸 여백(quiet zone)을 둬야 인식이 잘 됨.

    매개변수:
        pen: 그릴 _Pen
        payload: QR에 넣을 문자열 (layout.make_qr_payload() 결과, 예: "CM1|EX-...|P20Q5C|1|과목|시험")
        bbox: QR을 그릴 영역 [x, y, w, h] (mm). 정사각형이라고 보고 폭(w)만 씀
    """
    p = cv2.QRCodeEncoder.Params()
    p.correction_level = cv2.QRCodeEncoder_CORRECT_LEVEL_M
    img = cv2.QRCodeEncoder.create(p).encode(payload)   # 1px = 1 module, UTF-8 그대로 들어감
    # 인코더가 붙여 준 흰 여백을 잘라내고 실제 QR 패턴(검은 모듈이 있는 범위)만 남김
    ys, xs = (img < 128).nonzero()
    core = img[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    n = core.shape[0]                  # QR 한 변의 모듈 개수
    quiet = 2                          # 둘레 여백 모듈 수 (한쪽 기준)
    x, y, w, _ = bbox
    cell = w / (n + 2 * quiet)         # 모듈 한 칸의 한 변 길이 (mm). 여백까지 포함해서 bbox 폭에 맞춤
    # 검은 모듈만 사각형으로 그림 (흰 바탕은 종이 그대로)
    for r in range(n):
        for c in range(n):
            if core[r, c] < 128:
                pen.rect([x + (c + quiet) * cell, y + (r + quiet) * cell, cell, cell], fill=black, stroke=None)


def render_pdf(layout: dict, exam: dict, out_path: str, font_path: str | None = None,
               show_exam_info: bool = False) -> None:
    """PDF 생성 메인 함수.
    exam: {"exam_id", "exam_name", "subject", "qr_payload"} (service.create_answer_sheet에서 만듦)
    show_exam_info: True면 시험명·과목명도 인쇄 (회의 결정상 기본은 QR에만 넣음)

    그리는 순서: 1) ArUco 마커 → 2) 제목 → 3) 바깥 테두리 → 4) 이름·QR 패널 → 5) 학번 → 6) 객관식 → 7) 주관식 → 8) 바닥글

    매개변수:
        layout: compute_layout()이 만든 layout dict (frame, title_center_mm, sheet_frame_bbox_mm, info_header_bbox_mm,
                info_panel_bbox_mm, name_box, qr_box, header_align, student_id, mcq, short_answers,
                template_id, template_version 키를 씀)
        exam: 시험 정보 dict. 꼭 필요한 키는 "exam_id", "qr_payload".
              "exam_name", "subject"는 없으면 빈 문자열로 처리
        out_path: 저장할 PDF 파일 경로 (예: "storage/exams/EX-.../answer_sheet.pdf")
        font_path: 직접 지정할 한글 .ttf 글꼴 경로 (없으면 register_font()의 기본 순서대로 찾음)
        show_exam_info: True면 제목 아래에 과목명·시험명을 회색 글자로 인쇄
    반환:
        없음 (out_path에 PDF 파일이 생김)
    예외:
        KeyError: layout 또는 exam에 필요한 키가 없을 때
    """
    font = register_font(font_path)
    fr = layout["frame"]                        # 마커·QR 고정 위치 (frame.frame_dict() 결과)
    W, H = fr["page_size_mm"]                   # 용지 가로·세로 (mm)
    c = canvas.Canvas(out_path, pagesize=(W * mm, H * mm))
    # PDF 문서 정보(제목·작성자·주제) 설정. PDF 뷰어의 '문서 속성'에 보임
    c.setTitle(f"OMR 답안지 {exam.get('exam_name', '')} ({exam['exam_id']})")
    c.setAuthor("CheckMate")
    c.setSubject(f"{layout['template_id']} v{layout['template_version']}")
    pen = _Pen(c, font, H)

    # 1) ArUco 마커 4개 — corners_mm[0] = 마커 왼쪽 위 꼭짓점
    for m in fr["aruco"]["markers"]:
        x, y = m["corners_mm"][0]
        _draw_aruco(pen, m["id"], x, y, fr["aruco"]["marker_size_mm"])

    # 2) 제목 (시험명·과목명은 QR에만 넣고 기본적으로 인쇄하지 않음)
    tx, ty = layout["title_center_mm"]
    pen.text(tx, ty + 3, "OMR 답안지", size=24, align="center")
    if show_exam_info:
        pen.text(tx, ty + 8, f"{exam.get('subject', '')}  {exam.get('exam_name', '')}",
                 size=8, align="center", color=GRAY)

    ha = layout["header_align"]                 # 머리글 글자 정렬 (가로 양식 "center" / 세로 양식 "left")
    hsize = 11 if ha == "center" else 10        # 머리글 글자 크기 (pt)
    # 3) 바깥 큰 테두리 + 정보 패널 위 빈 머리글 칸 (가로 양식만 있음)
    if layout["sheet_frame_bbox_mm"]:
        pen.rect(layout["sheet_frame_bbox_mm"], lw=0.85)
    if layout["info_header_bbox_mm"]:
        pen.rect(layout["info_header_bbox_mm"], fill=HEADER_FILL, lw=0.85)

    # 4) 이름 / QR 패널: 패널 테두리 → 이름 글자 칸(회색) → 이름 쓰는 칸 → QR 패널·네모 칸 → QR
    pen.rect(layout["info_panel_bbox_mm"], lw=0.85)
    nb = layout["name_box"]
    pen.rect(nb["label_bbox_mm"], fill=HEADER_FILL, lw=0.6)
    pen.text_in(nb["label_bbox_mm"], nb["label"], 10)
    pen.rect(nb["bbox_mm"], lw=0.6)
    qb = layout["qr_box"]
    pen.rect(qb["panel_bbox_mm"], lw=0.6)
    if qb["label_bbox_mm"]:
        pen.text_in(qb["label_bbox_mm"], "QR 코드", 8)
    pen.rect(qb["bbox_mm"], lw=0.6)
    _draw_qr(pen, exam["qr_payload"], fr["qr"]["bbox_mm"])   # QR은 네모 칸 안 가운데 (frame.py 고정 위치)

    # 5) 학번: 머리글 + 손글씨 칸 + 자리별 줄 칸 + 0~9 동그라미 (동그라미 안 회색 숫자는 학생 안내용)
    sid = layout["student_id"]
    pen.rect(sid["bbox_mm"], lw=0.85)
    pen.rect(sid["header_bbox_mm"], fill=HEADER_FILL, lw=0.6)
    pen.text_in(sid["header_bbox_mm"], "학번", 10)
    for wb in sid["write_boxes"]:
        pen.rect(wb["bbox_mm"], lw=0.4)
    for cb in sid["column_boxes"]:
        pen.rect(cb["bbox_mm"], lw=0.4)
    for b in sid["bubbles"]:
        cx, cy = b["center_mm"]
        pen.circle(cx, cy, b["radius_mm"], lw=0.55)
        # 숫자 baseline을 중심보다 0.8mm 아래로 → 5pt 숫자가 동그라미 가운데쯤에 오게
        pen.text(cx, cy + 0.8, str(b["value"]), size=5, align="center", color=GRAY)

    # 6) 객관식: 영역 테두리, 머리글, 문항 수, 행 칸·번호 칸, 선지 동그라미
    mcq = layout["mcq"]
    pen.rect(mcq["bbox_mm"], lw=0.85)
    pen.rect(mcq["header_bbox_mm"], fill=HEADER_FILL, lw=0.85)
    pen.text_in(mcq["header_bbox_mm"], "객관식", hsize, align=ha)
    pen.text_in(mcq["count_label_bbox_mm"], f"문항 수: {mcq['num_questions']}", 7.5,
                align="right" if ha == "left" else "left")
    for q in mcq["questions"]:
        pen.rect(q["row_bbox_mm"], lw=0.4)
        pen.rect(q["number_bbox_mm"], lw=0.4)
        pen.text_in(q["number_bbox_mm"], str(q["q"]), 7)
        for ch in q["choices"]:
            cx, cy = ch["center_mm"]
            pen.circle(cx, cy, ch["radius_mm"], lw=0.55)
            pen.text(cx, cy + 0.85, str(ch["choice"]), size=5.5, align="center", color=GRAY)
    # 열마다 바깥 테두리 (행 칸보다 굵게)
    for col in mcq["columns"]:
        pen.rect(col["bbox_mm"], lw=0.8)

    # 7) 주관식: 영역 테두리, 머리글, 번호 칸 + 답 칸 (답 칸 안쪽 answer_bbox가 OCR 영역)
    sa = layout["short_answers"]
    pen.rect(sa["bbox_mm"], lw=0.85)
    pen.rect(sa["header_bbox_mm"], fill=HEADER_FILL, lw=0.85)
    pen.text_in(sa["header_bbox_mm"], "주관식", hsize, align=ha)
    for it in sa["items"]:
        pen.rect(it["number_bbox_mm"], lw=0.6)
        pen.text_in(it["number_bbox_mm"], str(it["no"]), 9)
        pen.rect(it["box_bbox_mm"], lw=0.6)

    # 8) 바닥글: 시험 ID·양식 ID (QR이 훼손돼도 사람이 확인 가능), 100% 인쇄 안내
    #    위치: 가로 가운데, 용지 아래 끝에서 12mm 위
    pen.text(W / 2, H - 12, f"{exam['exam_id']} · {layout['template_id']} v{layout['template_version']} · "
             f"인쇄 시 '실제 크기(100%)'로 인쇄하세요", size=6.5, align="center", color=GRAY)
    # 페이지를 마무리하고 파일로 저장
    c.showPage()
    c.save()
