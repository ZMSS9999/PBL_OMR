# pyright: basic
"""답안지 입력값(SheetSpec) → 양식(template) 좌표 계산. 파이프라인의 출발점.

[이 파일의 역할]
  문항 수·선지 수·방향을 받아서 답안지 위 모든 칸(이름, 학번, 객관식 동그라미, 주관식 칸)의
  위치를 mm 단위로 계산함. 그림은 그리지 않고 '좌표 숫자'만 만듦 (그리기는 render_pdf.py)

[파이프라인에서 위치]
  준비 단계(답안지 생성) → P1 정렬 → P2 시험 확인 → P3 판독 → P4 채점 → P5 엑셀
  이 파일은 '준비 단계'. 시험을 만들 때 1번 실행되고, 결과 layout은 P1~P3에서 계속 다시 읽힘

[입력 → 출력]
  SheetSpec(문항 수, 선지 수, 방향) ──compute_layout()──► layout(dict) ──► layout.json으로 저장
  이 layout이 이후 모든 단계의 '지도' 역할: PDF 그리기, 칸 잘라내기, 판독이 전부 이 좌표만 봄

[다른 파일과의 관계]
  - 이 파일이 부르는 것: sheetgen/frame.py (마커·QR 고정 위치, 기준 dpi, QR 표식)
  - 이 파일을 부르는 곳:
      app/service.py   : SheetSpec, compute_layout(), make_qr_payload() → 답안지 생성·layout.json 저장
      app/main.py      : NUM_QUESTIONS_RANGE, NUM_CHOICES_RANGE (/api/sheet-options), LayoutOverflowError
      pipeline/align.py: parse_qr_payload(), mm_to_px(), bbox_to_px() (P1 정렬·QR 해석·칸 잘라내기)
      pipeline/read.py : mm_to_px() (P3 판독)
      tools/smoke_test.py : all_bubbles(), bbox_to_px(), mm_to_px() (정렬 오차 검증)
      sheetgen/render_pdf.py : 직접 import하지는 않고 compute_layout()의 결과 dict를 받아 그림

[팀 양식 (2026-10-14부터, TEMPLATE_VERSION 2)]
  - 칸 위치·크기는 팀 pptx(PBL 문서/PDF 생성기 양식)와 좌표 JSON(PBL 문서/json양식)을 그대로 따름
  - 45문항·5지선다면 객관식 동그라미 중심, 마커, 서술형 칸(subjective_answers)이 json과 똑같음
    (합성 데이터셋도 같은 좌표. 확인: tools/check_team_form.py)
  - 치수 표: TEAM_FORM (방향별), MCQ_BUBBLE_R, SID_BUBBLE_R

[3주차 회의 결정 반영]
  - 객관식 문항 수 5~45, 선지 수 2~5 (웹에서 입력)
  - 주관식 5문항 고정 (단답형~한 문장)
  - 시험지에 쓰는 것은 이름·학번뿐, 시험명·과목명은 QR에만 저장
  - 세로(portrait) / 가로(landscape) 두 가지 배치
  - JSON 항목명: template_id, template_version

[양식 vs 시험 구분]
  - 양식(template): '칸 배치'만 담음. 같은 배치를 여러 시험이 같이 씀 (예: P20Q5C)
  - 시험(exam)    : exam_id, 시험명, 과목명. QR과 exam.json에 들어감 (service.py)

[layout dict 주요 키] (다른 모듈이 읽는 부분)
  frame                       마커·QR 위치 (frame.py)
  student_id.bubbles[]        학번 칸: digit_index(몇째 자리), value(0~9), center_mm, radius_mm, bbox_mm
  mcq.questions[]             객관식: q(문항 번호), choices[]: choice(1~k), center_mm, radius_mm, bbox_mm
  short_answers.items[]       주관식: no, answer_bbox_mm(OCR에 넘길 영역)
  (전체 키 목록은 compute_layout() 설명 참고)

[좌표 표기 규칙]
  - 모든 좌표 단위는 mm, 원점은 용지 왼쪽 위 (frame.py와 같음)
  - 이름이 *_bbox_mm 인 값은 사각형 [x, y, w, h] = [왼쪽 위 x, 왼쪽 위 y, 폭, 높이]
  - center_mm 는 [x, y] 중심점, radius_mm 는 반지름

[주요 함수·클래스]
  SheetSpec              : 답안지 생성 입력값 (문항 수, 선지 수, 방향 등) + 범위 검사 + template_id
  LayoutOverflowError    : 칸이 용지에 다 안 들어갈 때 나는 오류
  compute_layout()       : 메인 함수. SheetSpec → layout dict
  _portrait() / _landscape() : 방향별 전체 배치
  TEAM_FORM              : 방향별 팀 양식 치수 표 (객관식 열 위치, 행 높이, 학번 격자 등)
  _student_id_block() / _mcq_block() / _short_answer_block() : 영역별 칸 좌표 계산
  _bubble(), _r()        : 동그라미 한 개 dict 만들기, 소수점 반올림
  make_qr_payload() / parse_qr_payload() : QR 문자열 만들기 / 해석
  mm_to_px() / bbox_to_px() : mm 좌표 → 이미지 px 변환
  all_bubbles()          : 판독 대상 동그라미 전부 꺼내기

[사용 예]
  spec = SheetSpec(num_questions=20, num_choices=5)          # 세로, 20문항, 5지선다
  layout = compute_layout(spec)                              # layout["template_id"] == "P20Q5C"
  q1 = layout["mcq"]["questions"][0]                         # 1번 문항
  q1["choices"][2]["center_mm"]                              # 1번 문항 3번 선지 중심 [x, y] mm
  x, y, w, h = bbox_to_px(q1["choices"][2]["bbox_mm"])       # 정렬 이미지에서 자를 px 영역
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass

from . import frame as F

SCHEMA_VERSION = "0.2"   # layout.json 구조 버전 (키 이름·구조를 바꾸면 올림)
TEMPLATE_VERSION = "2"   # 칸 배치 버전 (2 = 팀 양식 치수, 2026-10-14). 배치 계산 코드를 바꾸면 반드시 올릴 것 (옛 인쇄본과 좌표 섞임 방지)

# ---- 입력 범위 (프론트엔드도 /api/sheet-options로 같은 값을 받아 씀 → 숫자는 여기 한 곳에만) ----
NUM_QUESTIONS_RANGE = (5, 45)   # 객관식 문항 수 (최소, 최대) — 양 끝 포함
NUM_CHOICES_RANGE = (2, 5)      # 문항당 선지 수 (최소, 최대) — 양 끝 포함
NUM_SHORT_ANSWERS = 5           # 주관식 문항 수 (회의 결정으로 5문항 고정)
STUDENT_ID_DIGITS = 10   # 학번 자릿수 (아직 확정 전, 임시 10자리)
ROWS_PER_COLUMN = 15     # 객관식 한 열 15문항, 최대 3열 = 45문항

# ---- 팀 양식 치수 (mm) ----
# 출처: 팀 pptx 'OMR_A4_가로형_수정본' / 'OMR_A4_세로형_수정본'(PBL 문서/PDF 생성기 양식)과
#       좌표 JSON 'omr_marks.json' / 'omr_marks_vertical.json'(PBL 문서/json양식). 합성 데이터셋도 같은 좌표
# 45문항·5지선다면 객관식 동그라미 중심이 json과 똑같음. 문항·선지 수가 줄면 같은 격자에서 앞부분만 씀
# 값을 바꾸면 이미 인쇄한 답안지와 좌표가 어긋남 → TEMPLATE_VERSION을 반드시 올릴 것
MCQ_BUBBLE_R = 1.95      # 객관식 동그라미 반지름 (지름 3.9mm)
SID_BUBBLE_R = 1.75      # 학번 동그라미 반지름 (지름 3.5mm)

# 방향별 칸 배치표. 각 값의 뜻:
#   title_center      : 제목 "OMR 답안지" 가운데 [x, y]
#   mcq_col_x         : 객관식 열(1~15번, 16~30번, 31~45번) 왼쪽 x
#   mcq_row_top       : 객관식 첫 행 위쪽 y / mcq_row_h: 행 높이 / mcq_row_w: 행 폭(번호 칸 포함)
#   mcq_num_w         : 문항 번호 칸 폭
#   mcq_choice_center : 열 왼쪽 x → 선지 묶음 가운데(5지선다의 3번 선지 중심)까지 거리
#   mcq_choice_pitch  : 선지 동그라미 사이 간격 (중심 → 중심)
#   sid_header        : 학번 머리글 띠 [x, y, w, h]. 격자는 이 폭을 자릿수로 나눠 꽉 채움
#   sid_col_w         : 팀 양식의 학번 한 자리(세로 줄) 폭 = 줄 폭의 최솟값 / sid_write_y, sid_write_h: 손글씨 칸 위쪽 y·높이
#   sid_col_y, sid_col_h : 0~9 동그라미 줄 칸의 위쪽 y·높이
#   sid_row0_y        : 숫자 0 동그라미 중심 y / sid_row_pitch: 0→1→…→9 세로 간격
TEAM_FORM = {
    "landscape": {
        "title_center": [148.5, 15.0],
        "mcq_col_x": [83.78, 123.81, 163.9], "mcq_row_top": 48.5, "mcq_row_h": 8.6, "mcq_row_w": 40.1,
        "mcq_num_w": 8.0, "mcq_choice_center": 24.05, "mcq_choice_pitch": 5.5,
        "sid_header": [13.0, 65.0, 66.24, 8.0], "sid_col_w": 6.624,
        "sid_write_y": 73.0, "sid_write_h": 6.0, "sid_col_y": 79.0, "sid_col_h": 62.0,
        "sid_row0_y": 82.1, "sid_row_pitch": 6.2,
    },
    "portrait": {
        "title_center": [105.0, 15.0],
        "mcq_col_x": [8.0, 73.7, 139.4], "mcq_row_top": 104.0, "mcq_row_h": 6.5, "mcq_row_w": 62.6,
        "mcq_num_w": 8.0, "mcq_choice_center": 35.3, "mcq_choice_pitch": 6.6,
        "sid_header": [77.0, 29.0, 127.0, 8.0], "sid_col_w": 9.75,
        "sid_write_y": 37.0, "sid_write_h": 6.0, "sid_col_y": 43.0, "sid_col_h": 47.0,
        "sid_row0_y": 45.35, "sid_row_pitch": 4.7,
    },
}


class LayoutOverflowError(ValueError):
    """칸이 용지 안에 다 안 들어갈 때 나는 오류.

    ValueError를 상속하므로 `except ValueError`로도 잡힘.
    app/main.py에서 이 오류를 잡아 웹에 오류 응답으로 돌려줌.
    발생 위치: _student_id_block() (학번 자릿수 초과), _portrait() (주관식 행이 세로 높이 초과)
    """


@dataclass
class SheetSpec:
    """답안지 생성 입력값 (웹 폼 → 백엔드 → 여기).

    필드:
        num_questions: 객관식 문항 수 (정수, 5~45, 예: 20)
        num_choices: 문항당 선지 수 (정수, 2~5, 예: 5 → 5지선다)
        orientation: 용지 방향 ("portrait" 세로(기본) / "landscape" 가로)
        student_id_digits: 학번 자릿수 (기본 STUDENT_ID_DIGITS = 10)
        num_short_answers: 주관식 문항 수 (기본 NUM_SHORT_ANSWERS = 5)

    예:
        SheetSpec(num_questions=45, num_choices=5)  →  template_id "P45Q5C"
    """
    num_questions: int                              # 객관식 문항 수 (5~45)
    num_choices: int                                # 선지 수 (2~5)
    orientation: str = "portrait"                   # "portrait" 세로 / "landscape" 가로
    student_id_digits: int = STUDENT_ID_DIGITS      # 학번 자릿수 (기본 10)
    num_short_answers: int = NUM_SHORT_ANSWERS      # 주관식 문항 수 (기본 5)

    def validate(self):
        """범위 검사. 웹 화면·FastAPI에서도 막지만, 다른 경로(명령행 등)로 들어와도 여기서 한 번 더 막음.

        검사 항목: 문항 수가 NUM_QUESTIONS_RANGE 안의 정수인지, 선지 수가 NUM_CHOICES_RANGE 안의
        정수인지, 방향이 frame.ORIENTATIONS에 있는 값인지.
        (학번 자릿수·주관식 수는 검사하지 않음)

        반환:
            없음 (통과하면 그냥 끝남)
        예외:
            ValueError: 위 세 가지 중 하나라도 범위를 벗어났을 때
        """
        lo, hi = NUM_QUESTIONS_RANGE
        if not (isinstance(self.num_questions, int) and lo <= self.num_questions <= hi):
            raise ValueError(f"객관식 문항 수는 {lo}~{hi} 사이의 정수여야 합니다")
        lo, hi = NUM_CHOICES_RANGE
        if not (isinstance(self.num_choices, int) and lo <= self.num_choices <= hi):
            raise ValueError(f"선지 수는 {lo}~{hi} 사이의 정수여야 합니다")
        if self.orientation not in F.ORIENTATIONS:
            raise ValueError(f"orientation은 {list(F.ORIENTATIONS)} 중 하나")

    @property
    def template_id(self) -> str:
        """입력값으로 바로 만드는 양식 ID. 예: P45Q5C = 세로(P)·45문항·5지선다, 가로는 L.

        형식: (방향 첫 글자 대문자)(문항 수 2자리)Q(선지 수)C
        문항 수는 2자리로 맞춤 → 5문항이면 "P05Q4C"처럼 앞에 0이 붙음.
        같은 입력이면 항상 같은 ID → 같은 배치를 여러 시험이 같이 씀.

        반환:
            양식 ID 문자열 (예: "P20Q5C", "L45Q4C")
        """
        return f"{self.orientation[0].upper()}{self.num_questions:02d}Q{self.num_choices}C"


# ---------- 공통 블록 ----------
def _r(v):
    """좌표 소수점 3자리 반올림 (JSON 크기·해시 안정성용).

    부동소수점 계산 찌꺼기(예: 13.799999999)를 없애서 layout.json을 짧게 하고,
    같은 입력이면 geometry_hash가 항상 같게 만듦.

    매개변수:
        v: 반올림할 숫자 (mm, 예: 13.7999999)
    반환:
        소수점 3자리로 반올림한 값 (예: 13.8)
    """
    return round(v, 3)


def _bubble(cx, cy, r, **extra) -> dict:
    """동그라미 칸 하나: 중심·반지름 + 감싸는 사각형(bbox). extra에 q/choice 같은 식별 정보.

    매개변수:
        cx, cy: 동그라미 중심 좌표 (mm)
        r: 반지름 (mm, 객관식 MCQ_BUBBLE_R = 1.95 / 학번 SID_BUBBLE_R = 1.75)
        **extra: 칸을 구분하는 정보. 결과 dict 앞쪽에 그대로 들어감
                 (예: 객관식은 choice=3, 학번은 digit_index=0, value=7)
    반환:
        dict (예: {"choice": 3, "center_mm": [x, y], "radius_mm": 1.95, "bbox_mm": [x-r, y-r, 3.9, 3.9]})
          center_mm : 중심 [x, y] mm
          radius_mm : 반지름 mm
          bbox_mm   : 동그라미를 딱 감싸는 정사각형 [x, y, w, h] mm (판독 때 잘라낼 영역)
    """
    return {**extra, "center_mm": [_r(cx), _r(cy)], "radius_mm": r,
            "bbox_mm": [_r(cx - r), _r(cy - r), _r(2 * r), _r(2 * r)]}


def _student_id_block(g: dict, digits: int) -> dict:
    """학번 영역: 머리글 + 손으로 쓰는 칸 + 0~9 마킹 격자 (팀 양식 치수).

    자리(digit)마다 세로 한 줄이고, 각 줄에 위에서부터 0, 1, ..., 9 동그라미가 있음.
    줄 높이·동그라미 간격은 팀 양식 값 그대로 쓰고, 줄 폭은 머리글 폭을 자릿수로 나눠 꽉 채움.
    (가로 양식은 팀 양식과 같은 10자리. 세로 양식 팀 pptx는 13칸이지만 회의 결정 10자리라 줄 폭을 넓힘)

    매개변수:
        g: TEAM_FORM[방향] 배치표
        digits: 학번 자릿수 (예: 10)
    반환:
        학번 블록 dict. 키:
          digits         : 자릿수
          bbox_mm        : 학번 영역 전체 [x, y, w, h] (머리글 위쪽 ~ 동그라미 줄 아래쪽)
          header_bbox_mm : 머리글("학번") 띠 영역
          grid_x_mm      : 격자 좌우 끝 [왼쪽 x, 오른쪽 x]
          write_boxes    : 손글씨 칸 목록 [{"digit_index", "bbox_mm"}, ...]
          column_boxes   : 자리별 동그라미 줄 테두리 [{"digit_index", "bbox_mm"}, ...] (그리기용)
          bubbles        : 동그라미 목록 [{"digit_index", "value", "center_mm", "radius_mm", "bbox_mm"}, ...]
                           (자릿수 × 10개, 순서: 0번째 자리의 0~9, 1번째 자리의 0~9, ...)
    예외:
        LayoutOverflowError: 줄 폭이 팀 양식 줄 폭(sid_col_w)보다 좁아질 만큼 자릿수가 많을 때
                             (가로 양식 최대 10자리, 세로 양식 최대 13자리)
    """
    hx, hy, hw, _ = g["sid_header"]           # 머리글 띠 왼쪽 x, 위쪽 y, 폭
    cw = hw / digits                          # 한 자리 줄 폭 (머리글 폭을 꽉 채움)
    if cw < g["sid_col_w"] - 0.01:
        raise LayoutOverflowError(f"학번 {digits}자리가 학번 칸 폭({hw}mm)에 안 들어감")
    gx = hx                                   # 격자 왼쪽 끝 = 머리글 왼쪽 끝
    write_boxes, column_boxes, bubbles = [], [], []
    for d in range(digits):
        x0 = gx + cw * d                      # d번째 자리 줄 왼쪽 x
        write_boxes.append({"digit_index": d, "bbox_mm": [_r(x0), g["sid_write_y"], _r(cw), g["sid_write_h"]]})
        column_boxes.append({"digit_index": d, "bbox_mm": [_r(x0), g["sid_col_y"], _r(cw), g["sid_col_h"]]})
        for v in range(10):                   # 위에서부터 0, 1, ..., 9
            bubbles.append(_bubble(x0 + cw / 2, g["sid_row0_y"] + g["sid_row_pitch"] * v, SID_BUBBLE_R,
                                   digit_index=d, value=v))
    bottom = g["sid_col_y"] + g["sid_col_h"]  # 영역 아래 끝 y
    return {
        "digits": digits,
        "bbox_mm": [_r(hx), _r(hy), _r(hw), _r(bottom - hy)],
        "header_bbox_mm": list(g["sid_header"]),
        "grid_x_mm": [_r(gx), _r(gx + cw * digits)],
        "write_boxes": write_boxes,     # 손글씨 학번 칸: 판독 대상 아님 (검수 때 참고용)
        "column_boxes": column_boxes,
        "bubbles": bubbles,
    }


def _mcq_block(g: dict, n: int, k: int, header_bbox, bbox, count_label_bbox) -> dict:
    """객관식 영역: 최대 3열 × 15행 표 (팀 양식 치수).

    열 위치·행 높이는 문항 수와 상관없이 고정 → 문항 수가 달라도 1번 칸 위치는 같음.
    1~15번은 1열, 16~30번은 2열, 31~45번은 3열에 위에서 아래로 채움.
    각 행은 [문항 번호 칸 | 선지 동그라미 k개]. 선지 묶음 가운데를 팀 양식 5지선다의 3번 선지 자리에 맞춤
    → 5지선다면 json 좌표와 똑같고, 선지가 적으면 같은 간격으로 가운데에 모임.

    매개변수:
        g: TEAM_FORM[방향] 배치표
        n: 객관식 문항 수 (예: 20)
        k: 선지 수 (예: 5)
        header_bbox: 머리글("객관식") 띠 [x, y, w, h]
        bbox: 객관식 영역 전체 [x, y, w, h] (테두리용)
        count_label_bbox: "문항 수: N" 글자 칸 [x, y, w, h]
    반환:
        객관식 블록 dict. 키:
          num_questions, num_choices, rows_per_column : 문항 수, 선지 수, 열당 행 수(15)
          bbox_mm, header_bbox_mm, count_label_bbox_mm : 영역 전체 / 머리글 띠 / 문항 수 글자 칸
          columns   : 실제로 쓰는 열 목록 [{"column": 열 번호(0부터), "bbox_mm": 그 열의 표 영역}, ...]
          questions : 문항 목록. 각 항목 키:
                      q(문항 번호, 1부터), type("mcq"), column(열 번호, 0부터), row(열 안 행 번호, 0부터),
                      row_bbox_mm(행 전체), number_bbox_mm(번호 칸),
                      choices(선지 동그라미 목록, 각각 choice(1~k)·center_mm·radius_mm·bbox_mm)
    """
    top, rh, rw = g["mcq_row_top"], g["mcq_row_h"], g["mcq_row_w"]   # 첫 행 위쪽 y, 행 높이, 행 폭
    pitch = g["mcq_choice_pitch"]
    questions, columns = [], []
    for c in range(math.ceil(n / ROWS_PER_COLUMN)):                    # 실제로 쓰는 열만
        cx0 = g["mcq_col_x"][c]                # c번째 열 왼쪽 x
        first = cx0 + g["mcq_choice_center"] - pitch * (k - 1) / 2   # 1번 선지 중심 x
        qs = range(c * ROWS_PER_COLUMN, min(n, (c + 1) * ROWS_PER_COLUMN))   # 이 열에 들어갈 문항 인덱스(0부터)
        for r, qi in enumerate(qs):
            ry = top + rh * r                  # 행 위쪽 y
            cy = ry + rh / 2                   # 행 가운데 y (동그라미 중심)
            questions.append({
                "q": qi + 1, "type": "mcq", "column": c, "row": r,
                "row_bbox_mm": [_r(cx0), _r(ry), rw, rh],
                "number_bbox_mm": [_r(cx0), _r(ry), g["mcq_num_w"], rh],
                "choices": [_bubble(first + pitch * j, cy, MCQ_BUBBLE_R, choice=j + 1) for j in range(k)],
            })
        columns.append({"column": c, "bbox_mm": [_r(cx0), _r(top), rw, _r(rh * len(qs))]})
    return {
        "num_questions": n, "num_choices": k, "rows_per_column": ROWS_PER_COLUMN,
        "bbox_mm": list(bbox), "header_bbox_mm": list(header_bbox),
        "count_label_bbox_mm": list(count_label_bbox),
        "columns": columns, "questions": questions,
    }


def _short_answer_block(x, y, w, row_h, count, num_w, header_bbox, bbox) -> dict:
    """주관식 영역: (번호 칸 | 답 칸) × count 행.
    5문항이면 답 칸(box_bbox_mm)이 팀 좌표 JSON의 subjective_answers(서술형 칸)와 같음.

    매개변수:
        x, y: 첫 행 왼쪽 위 좌표 (mm)
        w: 행 폭 (번호 칸 포함, mm)
        row_h: 한 문항(행) 높이 (mm, 세로 양식 10.2 / 가로 양식 29.6)
        count: 주관식 문항 수 (예: 5)
        num_w: 번호 칸 폭 (mm, 세로 양식 12.0 / 가로 양식 7.57)
        header_bbox: 머리글("주관식") 띠 [x, y, w, h]
        bbox: 주관식 영역 전체 [x, y, w, h] (테두리용)
    반환:
        주관식 블록 dict. 키:
          bbox_mm, header_bbox_mm : 영역 전체 / 머리글("주관식") 띠
          items : 문항 목록. 각 항목 키:
                  no(문항 번호, 1부터), type("short_answer"),
                  number_bbox_mm(번호 칸), box_bbox_mm(답 칸 전체, 테두리 포함),
                  answer_bbox_mm(답 칸 안쪽, OCR에 넘길 영역)
    """
    items = []
    pad = 1.2                      # 테두리 선 두께 + 여유. OCR 영역에서 빼는 폭
    for i in range(count):
        ry = y + row_h * i         # i번째 행 위쪽 y
        items.append({
            "no": i + 1, "type": "short_answer",
            "number_bbox_mm": [_r(x), _r(ry), num_w, _r(row_h)],
            "box_bbox_mm": [_r(x + num_w), _r(ry), _r(w - num_w), _r(row_h)],
            # OCR에 넘길 영역 = 테두리 선을 뺀 안쪽 (선이 글자로 인식되는 것 방지)
            "answer_bbox_mm": [_r(x + num_w + pad), _r(ry + pad), _r(w - num_w - 2 * pad), _r(row_h - 2 * pad)],
        })
    return {"bbox_mm": list(bbox), "header_bbox_mm": list(header_bbox), "items": items}


# ---------- 방향별 배치 ----------
def _portrait(spec: SheetSpec) -> dict:
    """세로 배치 (팀 양식 'OMR_A4_세로형_수정본'): 위쪽 [이름+QR | 학번] → 객관식 → 주관식.

    매개변수:
        spec: 답안지 입력값 (num_questions, num_choices, student_id_digits, num_short_answers 사용)
    반환:
        배치 dict. 키:
          title_center_mm     : 제목("OMR 답안지") 가운데 [x, y] mm
          sheet_frame_bbox_mm : 전체 바깥 테두리 (세로 양식은 없음 → None)
          info_header_bbox_mm : 정보 패널 위 빈 머리글 칸 (세로 양식은 없음 → None)
          info_panel_bbox_mm  : 왼쪽 위 정보 패널(이름+QR) 테두리 [x, y, w, h]
          name_box            : {"label": "이름", "label_bbox_mm": 글자 칸, "bbox_mm": 이름 쓰는 칸}
          qr_box              : {"panel_bbox_mm": QR 패널, "label_bbox_mm": "QR 코드" 글자 칸(없으면 None),
                                 "bbox_mm": QR 네모 테두리} (QR 자체 위치는 frame.qr)
          header_align        : 머리글 글자 정렬 ("left" / "center")
          student_id          : _student_id_block() 결과
          mcq                 : _mcq_block() 결과
          short_answers       : _short_answer_block() 결과
    예외:
        LayoutOverflowError: 주관식 행이 아래쪽 마커 영역(272mm)을 넘거나 학번 자릿수가 너무 많을 때
    """
    g = TEAM_FORM["portrait"]
    sa_top, sa_row_h = 220.0, 10.2                    # 주관식 첫 행 위쪽 y, 행 높이
    sa_bottom = sa_top + sa_row_h * spec.num_short_answers
    if sa_bottom > 272.0:                             # 아래쪽 마커(274.4mm~)와 겹치지 않게
        raise LayoutOverflowError(f"세로 양식 높이 초과 ({sa_bottom - 272.0:.1f}mm)")
    return {
        "title_center_mm": g["title_center"],
        "sheet_frame_bbox_mm": None, "info_header_bbox_mm": None,
        "info_panel_bbox_mm": [8.0, 29.0, 69.0, 61.0],
        "name_box": {"label": "이름", "label_bbox_mm": [8.0, 29.0, 18.0, 20.86], "bbox_mm": [26.0, 29.0, 51.0, 20.86]},
        "qr_box": {"panel_bbox_mm": [8.0, 49.86, 69.0, 40.14], "label_bbox_mm": None,
                   "bbox_mm": [25.0, 52.79, 35.0, 35.0]},
        "header_align": "left",
        "student_id": _student_id_block(g, spec.student_id_digits),
        "mcq": _mcq_block(g, spec.num_questions, spec.num_choices, header_bbox=[8.0, 94.0, 194.0, 10.0],
                          bbox=[8.0, 94.0, 194.0, 107.5], count_label_bbox=[148.0, 94.0, 52.0, 10.0]),
        "short_answers": _short_answer_block(8.0, sa_top, 194.01, sa_row_h, spec.num_short_answers, 12.0,
                                             header_bbox=[8.0, 210.0, 194.0, 10.0],
                                             bbox=[8.0, 210.0, 194.0, _r(sa_bottom - 210.0)]),
    }


def _landscape(spec: SheetSpec) -> dict:
    """가로 배치 (팀 양식 'OMR_A4_가로형_수정본'): 왼쪽 [성명·학번·QR] | 가운데 객관식 | 오른쪽 주관식.

    큰 바깥 테두리 안을 세 칸으로 나누고, 위쪽 11mm 띠가 머리글(빈 칸 / 객관식 / 주관식).

    매개변수:
        spec: 답안지 입력값 (num_questions, num_choices, student_id_digits, num_short_answers 사용)
    반환:
        배치 dict. 키 구성은 _portrait()와 같음
    예외:
        LayoutOverflowError: 학번 자릿수가 너무 많을 때 (가로 양식은 최대 10자리)
    """
    g = TEAM_FORM["landscape"]
    sa_row_h = 148.0 / spec.num_short_answers         # 주관식 행 높이: 표 높이(148mm)를 문항 수로 나눔
    return {
        "title_center_mm": g["title_center"],
        "sheet_frame_bbox_mm": [9.0, 28.0, 279.0, 159.0],
        "info_header_bbox_mm": [9.0, 28.0, 71.62, 11.0],
        "info_panel_bbox_mm": [9.0, 39.0, 71.62, 148.0],
        "name_box": {"label": "성명", "label_bbox_mm": [13.0, 45.0, 15.0, 14.0], "bbox_mm": [28.0, 45.0, 50.05, 14.0]},
        "qr_box": {"panel_bbox_mm": [13.0, 145.0, 53.0, 37.0], "label_bbox_mm": [14.0, 148.0, 17.0, 9.0],
                   "bbox_mm": [32.0, 148.5, 30.0, 30.0]},
        "header_align": "center",
        "student_id": _student_id_block(g, spec.student_id_digits),
        "mcq": _mcq_block(g, spec.num_questions, spec.num_choices, header_bbox=[80.62, 28.0, 123.38, 11.0],
                          bbox=[80.62, 28.0, 123.38, 159.0], count_label_bbox=[84.0, 39.0, 43.0, 8.0]),
        "short_answers": _short_answer_block(204.17, 39.0, 83.82, _r(sa_row_h), spec.num_short_answers, 7.57,
                                             header_bbox=[204.0, 28.0, 84.0, 11.0],
                                             bbox=[204.0, 28.0, 84.0, 159.0]),
    }


def compute_layout(spec: SheetSpec) -> dict:
    """양식 좌표 계산 (메인 함수). 같은 입력이면 항상 같은 결과 → 같은 template_id면 같은 좌표.

    처리 순서:
      1. spec.validate()로 입력 범위 검사
      2. 방향에 맞는 배치 함수(_portrait / _landscape)로 칸 좌표 계산
      3. frame.frame_dict()로 마커·QR 고정 위치 추가
      4. 좌표 전체로 geometry_hash(지문) 계산

    매개변수:
        spec: 답안지 입력값 SheetSpec (예: SheetSpec(num_questions=20, num_choices=5))
    반환:
        layout dict (그대로 layout.json으로 저장됨). 키:
          schema_version   : layout.json 구조 버전 (SCHEMA_VERSION, 예: "0.2")
          template_id      : 양식 ID (예: "P20Q5C")
          template_version : 칸 배치 버전 (TEMPLATE_VERSION, 예: "2")
          geometry_hash    : 좌표 전체의 SHA-1 해시 앞 12글자
          mode             : "A" (자체 양식)
          orientation      : "portrait" / "landscape"
          spec             : 입력값 SheetSpec을 dict로 바꾼 것
          frame            : 마커·QR 위치 (frame.frame_dict() 결과)
          title_center_mm, sheet_frame_bbox_mm, info_header_bbox_mm, info_panel_bbox_mm, name_box, qr_box,
          header_align, student_id, mcq, short_answers
                           : 방향별 배치 결과 (_portrait / _landscape 설명 참고)
    예외:
        ValueError: 입력 범위를 벗어났을 때 (spec.validate())
        LayoutOverflowError: 칸이 용지에 다 안 들어갈 때
    """
    spec.validate()
    # 방향에 따라 배치 함수를 골라서 바로 호출
    geometry = (_portrait if spec.orientation == "portrait" else _landscape)(spec)
    frame = F.frame_dict(spec.orientation)
    # 좌표 전체의 지문(해시). 같은 id·version인데 해시가 다르면 = 배치 코드가 바뀌었는데 버전을 안 올린 것
    # (sort_keys=True로 키 순서를 고정해야 같은 좌표면 항상 같은 해시가 나옴)
    geometry_hash = hashlib.sha1(json.dumps({"frame": frame, "g": geometry}, sort_keys=True)
                                 .encode()).hexdigest()[:12]
    return {
        "schema_version": SCHEMA_VERSION,
        "template_id": spec.template_id,
        "template_version": TEMPLATE_VERSION,
        "geometry_hash": geometry_hash,    # service.save_template()에서 저장본과 비교
        "mode": "A",                       # 모드 A = 자체 양식 (B: 등록 양식, C: 미등록 양식)
        "orientation": spec.orientation,
        "spec": asdict(spec),              # 입력값 기록 (예: {"num_questions": 20, "num_choices": 5, ...})
        "frame": frame,
        **geometry,                        # 방향별 배치 결과 키들을 최상위에 펼쳐 넣음
    }


# ---------- QR ----------
def make_qr_payload(exam_id: str, template_id: str, template_version: str, subject: str, exam_name: str) -> str:
    """QR 내용 만들기: CM1|시험ID|양식ID|양식버전|과목명|시험명 ('|'로 구분).

    app/service.py가 답안지를 만들 때 부르고, 결과 문자열은 render_pdf.py가 QR로 그림.
    P1 정렬 후 parse_qr_payload()로 다시 풀어서 P2 시험 확인에 씀.

    매개변수:
        exam_id: 시험 ID (예: "EX-20261001-8C7353")
        template_id: 양식 ID (예: "P45Q5C")
        template_version: 양식 버전 (예: "1")
        subject: 과목명 (빈 문자열 가능, 예: "컴퓨터비전")
        exam_name: 시험명 (빈 문자열 가능, 예: "2026학년도 2학기 중간고사")
    반환:
        QR 문자열 (예: "CM1|EX-20261001-8C7353|P45Q5C|1|컴퓨터비전|2026학년도 2학기 중간고사")
    예외:
        ValueError: 시험 ID·과목명·시험명에 구분 문자 '|'가 들어 있을 때 (해석할 때 칸이 어긋나므로)
    """
    for v in (exam_id, subject, exam_name):
        if "|" in v:
            raise ValueError("시험 정보에는 '|' 문자를 쓸 수 없습니다")
    return "|".join([F.QR_PREFIX, exam_id, template_id, template_version, subject, exam_name])


def parse_qr_payload(text: str) -> dict | None:
    """QR 문자열 → {"exam_id", "template_id", "template_version", "subject", "exam_name"}.
    형식이 다르거나 우리 QR(CM1)이 아니면 None.

    make_qr_payload()의 반대 동작. pipeline/align.py(P1 정렬)가 QR을 읽은 뒤 부름.

    매개변수:
        text: QR에서 읽은 문자열 (예: "CM1|EX-20261001-8C7353|P45Q5C|1|컴퓨터비전|중간고사")
    반환:
        dict (예: {"exam_id": "EX-20261001-8C7353", "template_id": "P45Q5C", "template_version": "1",
                   "subject": "컴퓨터비전", "exam_name": "중간고사"})
        또는 '|'로 나눈 조각이 6개가 아니거나 첫 조각이 "CM1"이 아니면 None
    """
    parts = text.split("|")
    if len(parts) != 6 or parts[0] != F.QR_PREFIX:
        return None
    # 첫 조각(CM1 표식)은 빼고 나머지 5개를 이름과 짝지음
    return dict(zip(["exam_id", "template_id", "template_version", "subject", "exam_name"], parts[1:]))


# ---------- 좌표 변환 도우미 (다른 파트에서 import해서 사용) ----------
def mm_to_px(v_mm: float, dpi: int = F.CANONICAL_DPI) -> float:
    """mm → px. 1inch = 25.4mm, 200dpi면 1mm ≈ 7.87px.

    매개변수:
        v_mm: 길이 또는 좌표 (mm, 예: 10.0)
        dpi: 해상도 (기본 200 = 정렬 이미지 기준)
    반환:
        px 값 (실수, 반올림 안 함. 예: mm_to_px(10.0) → 약 78.74)
    """
    return v_mm / 25.4 * dpi


def bbox_to_px(bbox_mm, dpi: int = F.CANONICAL_DPI) -> tuple[int, int, int, int]:
    """[x, y, w, h] mm → 정수 px. 이미지 자르기: img[y:y+h, x:x+w].

    매개변수:
        bbox_mm: 사각형 [x, y, w, h] (mm, 예: layout의 아무 *_bbox_mm 값)
        dpi: 해상도 (기본 200)
    반환:
        (x, y, w, h) 정수 px 튜플 (각 값을 따로 반올림, 예: [10, 20, 5, 5] mm → (79, 157, 39, 39))
    """
    x, y, w, h = (mm_to_px(v, dpi) for v in bbox_mm)
    return round(x), round(y), round(w), round(h)


def all_bubbles(layout: dict):
    """판독 대상 동그라미 전체 (객관식 + 학번). 정렬 오차 측정 등에 사용.

    제너레이터 함수라 for 문으로 하나씩 꺼내 씀 (tools/smoke_test.py에서 사용).

    매개변수:
        layout: compute_layout()이 만든 layout dict (또는 layout.json을 읽은 것)
    반환(하나씩 내놓음):
        동그라미 dict (_bubble() 형식). 객관식 1번 문항의 선지부터 마지막 문항까지, 그다음 학번 동그라미 순서
    """
    for q in layout["mcq"]["questions"]:
        yield from q["choices"]
    yield from layout["student_id"]["bubbles"]
