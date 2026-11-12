# pyright: basic
"""답안지 생성 서비스 (파이프라인 준비 단계): 입력값 검증 → 양식 좌표 저장 → 시험 ID 발급 → PDF 생성.

[역할]
  - 선생님이 고른 문항 수·선지 수·방향으로 답안지 PDF를 만들고, 그 기록을 storage/ 폴더에 저장함
  - 저장해 둔 시험 기록(exam.json)·양식 좌표(layout.json)·PDF를 다시 찾아 주는 함수도 여기 있음

[파이프라인에서의 위치]
  준비 단계 (P1 정렬보다 앞). 여기서 만든 exam과 layout이 채점 단계(P1~P5)의 기준 정보가 됨
    - layout(칸 좌표) : P2 시험 확인(방향 비교), P3 판독(칸 위치)에서 사용
    - exam(시험 기록) : P2 시험 확인(QR의 시험 ID 비교), P5 엑셀(시험명·과목명 표시)에서 사용

[입력 → 출력]
  문항 수·선지 수·방향·시험명 ──create_answer_sheet()──► exam(dict) + layout.json + PDF

[웹과 분리한 이유]
  FastAPI(main.py)는 요청을 받아 이 함수를 부르기만 함 → 테스트·명령행 도구에서도 같은 함수 그대로 사용

[다른 파일과의 관계]
  - 부르는 쪽: app/main.py (웹 API), app/grading_service.py (시험·양식 읽기),
              tools/make_sheet.py, tools/demo_end_to_end.py (명령행)
  - 부르는 대상: sheetgen/ (SheetSpec 입력값, compute_layout 좌표 계산, make_qr_payload QR 내용, render_pdf PDF 그리기)

[저장 구조]
  storage/templates/<template_id>_v<version>.layout.json   ← 칸 배치 (여러 시험이 공유)
  storage/exams/<exam_id>/exam.json                        ← 시험 정보 + 사용한 양식
  storage/exams/<exam_id>/answer_sheet.pdf                 ← 인쇄용 답안지 (QR에 시험 정보 포함)
  (같은 시험 폴더 아래 정답표·판독 결과·엑셀은 grading_service.py가 저장)
  storage 위치는 환경변수 CHECKMATE_STORAGE로 바꿀 수 있음 (테스트 때 임시 폴더 사용 등)

[주요 함수]
  template_path()        양식 JSON 파일 경로 만들기
  save_template()        양식 좌표 저장 (이미 있으면 같은 좌표인지 확인)
  load_template()        양식 좌표 읽기
  new_exam_id()          시험 ID 새로 발급
  create_answer_sheet()  답안지 생성 메인 함수
  get_exam()             시험 기록 읽기
  exam_pdf_path()        시험 PDF 파일 경로 찾기

[사용 예] (backend 폴더에서)
    from app import service
    rec = service.create_answer_sheet(20, 5, "portrait", "중간고사", "컴퓨터비전")
    rec["exam_id"]                         # → "EX-20261013-8C7353"
    service.exam_pdf_path(rec["exam_id"])  # → ".../storage/exams/EX-20261013-8C7353/answer_sheet.pdf"
"""
from __future__ import annotations

import glob
import json
import os
import re
import secrets
import shutil
from datetime import datetime, timezone, timedelta

from sheetgen import SheetSpec, compute_layout, make_qr_payload, render_pdf

# backend 폴더 경로 (이 파일 = backend/app/service.py 이므로 두 단계 위)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 저장 폴더. 환경변수 CHECKMATE_STORAGE가 있으면 그 경로, 없으면 backend/storage
STORAGE_DIR = os.environ.get("CHECKMATE_STORAGE", os.path.join(BASE_DIR, "storage"))
# 양식 좌표(layout.json)를 모아 두는 폴더
TEMPLATE_DIR = os.path.join(STORAGE_DIR, "templates")
# 시험별 폴더(exam.json, PDF, 정답표, 판독 결과 등)를 모아 두는 폴더
EXAM_DIR = os.path.join(STORAGE_DIR, "exams")
# 한국 표준시(UTC+9). 시험 ID 날짜·생성 시각을 한국 시간으로 기록하기 위함
KST = timezone(timedelta(hours=9))

# 시험 ID 형식 검사용 정규식: "EX-" + 날짜 8자리 + "-" + 16진수 대문자 6자리 (예: EX-20261013-8C7353)
EXAM_ID_RE = re.compile(r"^EX-\d{8}-[0-9A-F]{6}$")
MAX_EXAM_NAME = 30   # 시험명 최대 글자 수
MAX_SUBJECT = 20     # 과목명 최대 글자 수


class TemplateConflictError(RuntimeError):
    """같은 template_id·version인데 저장된 좌표와 새로 계산한 좌표가 다를 때 발생.

    칸 배치 코드를 고치고 layout.py의 TEMPLATE_VERSION을 올리지 않은 경우임.
    그대로 진행하면 옛날에 인쇄한 답안지를 새 좌표로 잘못 판독하게 되므로 생성을 멈춤.
    웹에서는 main.py가 이 오류를 HTTP 500으로 바꿔 보냄.
    """
    pass


def template_path(template_id: str, version: str) -> str:
    """양식 JSON 파일 경로. 예: storage/templates/P20Q5C_v1.layout.json

    파일이 실제로 있는지는 확인하지 않고 경로 문자열만 만듦.

    매개변수:
        template_id: 양식 ID (예: "P20Q5C" = 세로·20문항·5지선다)
        version: 양식 버전 (예: "1")
    반환:
        파일 경로 문자열 (예: ".../storage/templates/P20Q5C_v1.layout.json")
    """
    return os.path.join(TEMPLATE_DIR, f"{template_id}_v{version}.layout.json")


def save_template(layout: dict) -> str:
    """같은 template_id·version이 이미 있으면 좌표가 같은지 확인하고 재사용.

    없으면 새로 저장함. 이미 있으면 geometry_hash(좌표 전체로 만든 지문 값)만 비교하고 파일은 다시 쓰지 않음.
    → 같은 양식(예: 세로·20문항·5지선다)을 쓰는 시험 여러 개가 파일 하나를 공유함.

    매개변수:
        layout: compute_layout()이 만든 양식 좌표 dict
                (필수 키: "template_id", "template_version", "geometry_hash")
    반환:
        저장된(또는 이미 있던) 양식 파일 경로 문자열
    예외:
        TemplateConflictError: 같은 id·version인데 저장본과 geometry_hash가 다름
    """
    os.makedirs(TEMPLATE_DIR, exist_ok=True)
    path = template_path(layout["template_id"], layout["template_version"])
    if os.path.exists(path):
        # 이미 저장된 양식 → 좌표가 바뀌지 않았는지만 확인
        with open(path, encoding="utf-8") as f:
            old = json.load(f)
        if old["geometry_hash"] != layout["geometry_hash"]:
            raise TemplateConflictError(
                f"{layout['template_id']} v{layout['template_version']}의 좌표가 저장본과 다릅니다. "
                f"배치 코드를 고쳤다면 layout.py의 TEMPLATE_VERSION을 올리세요.")
        return path
    # 처음 쓰는 양식 → 한글이 깨지지 않게(ensure_ascii=False) 보기 좋게 들여쓰기해서 저장
    with open(path, "w", encoding="utf-8") as f:
        json.dump(layout, f, ensure_ascii=False, indent=2)
    return path


def load_template(template_id: str, version: str) -> dict | None:
    """(template_id, version) → layout dict. 없으면 None.

    매개변수:
        template_id: 양식 ID (예: "P45Q5C")
        version: 양식 버전 (예: "1")
    반환:
        양식 좌표 dict (예: {"template_id": "P45Q5C", "orientation": "portrait", "mcq": {...}, ...})
        파일이 없으면 None
    """
    path = template_path(template_id, version)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def new_exam_id() -> str:
    """시험 ID 발급: EX-날짜-무작위 6자리 (예: EX-20261013-8C7353). 같은 날 여러 시험도 겹치지 않게.

    날짜는 한국 시간 기준. 뒤 6자리는 무작위 3바이트를 16진수 대문자로 바꾼 값 (약 1,600만 가지).

    매개변수:
        없음
    반환:
        시험 ID 문자열 (예: "EX-20261013-8C7353")
    """
    return f"EX-{datetime.now(KST):%Y%m%d}-{secrets.token_hex(3).upper()}"


def create_answer_sheet(num_questions: int, num_choices: int, orientation: str = "portrait",
                        exam_name: str = "", subject: str = "") -> dict:
    """답안지 생성 메인 함수. 순서: 입력 검사 → 양식 좌표 계산·저장 → 시험 ID 발급 → QR 내용 → PDF → exam.json

    처리 순서:
        1. 시험명·과목명 앞뒤 공백 제거 후 글자 수·'|' 문자 검사
        2. 문항 수·선지 수·방향으로 양식 좌표 계산 (범위 검사 포함) → templates 폴더에 저장
        3. 시험 ID 발급 → QR 내용 문자열 만들기
        4. 시험 폴더 만들고 PDF 그리기 → exam.json 저장

    매개변수:
        num_questions: 객관식 문항 수 (정수 5~45, 예: 20)
        num_choices: 선지 수 (정수 2~5, 예: 5)
        orientation: "portrait"(세로, 기본) 또는 "landscape"(가로)
        exam_name: 시험명 (선택, 30자 이하, 예: "2026학년도 2학기 중간고사")
        subject: 과목명 (선택, 20자 이하, 예: "컴퓨터비전")
    반환:
        시험 기록 dict (exam.json에 저장한 것과 같은 내용). 예:
        {"exam_id": "EX-20261013-8C7353", "exam_name": "중간고사", "subject": "컴퓨터비전",
         "created_at": "2026-10-13T14:03:22+09:00", "template_id": "P20Q5C", "template_version": "1",
         "orientation": "portrait", "num_questions": 20, "num_choices": 5,
         "qr_payload": "CM1|EX-20261013-8C7353|P20Q5C|1|컴퓨터비전|중간고사"}
    예외:
        ValueError: 시험명·과목명이 너무 길거나 '|' 포함, 문항 수·선지 수·방향이 범위 밖
        LayoutOverflowError: 칸이 용지 안에 다 안 들어감 (sheetgen에서 발생)
        TemplateConflictError: 같은 양식 id·version인데 저장된 좌표와 다름
    """
    exam_name, subject = exam_name.strip(), subject.strip()
    if len(exam_name) > MAX_EXAM_NAME or len(subject) > MAX_SUBJECT:
        raise ValueError(f"시험명은 {MAX_EXAM_NAME}자, 과목명은 {MAX_SUBJECT}자 이하")
    # '|' 는 QR 내용의 구분 문자라서 시험명·과목명 안에 있으면 QR을 잘못 나누게 됨
    if "|" in exam_name + subject:
        raise ValueError("시험명·과목명에는 '|' 문자를 쓸 수 없습니다")

    spec = SheetSpec(num_questions=num_questions, num_choices=num_choices, orientation=orientation)
    layout = compute_layout(spec)          # 범위 검사(5~45, 2~5)도 여기서
    save_template(layout)

    exam_id = new_exam_id()
    # QR 내용: "CM1|시험ID|양식ID|양식버전|과목명|시험명" → P2에서 다른 시험 답안지인지 확인할 때 씀
    qr = make_qr_payload(exam_id, layout["template_id"], layout["template_version"], subject, exam_name)
    exam_dir = os.path.join(EXAM_DIR, exam_id)
    os.makedirs(exam_dir, exist_ok=True)
    pdf_path = os.path.join(exam_dir, "answer_sheet.pdf")
    # 시험 기록 (exam.json 내용이자 이 함수의 반환값)
    record = {
        "exam_id": exam_id,                                  # 시험 ID
        "exam_name": exam_name,                              # 시험명 (공백 제거 후)
        "subject": subject,                                  # 과목명 (공백 제거 후)
        "created_at": datetime.now(KST).isoformat(timespec="seconds"),   # 생성 시각 (한국 시간, 초 단위)
        "template_id": layout["template_id"],                # 사용한 양식 ID
        "template_version": layout["template_version"],      # 사용한 양식 버전
        "orientation": orientation,                          # 용지 방향
        "num_questions": num_questions,                      # 객관식 문항 수
        "num_choices": num_choices,                          # 선지 수
        "qr_payload": qr,                                    # QR에 넣은 문자열
    }
    render_pdf(layout, record, pdf_path)
    with open(os.path.join(exam_dir, "exam.json"), "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    return record


def get_exam(exam_id: str) -> dict | None:
    """시험 기록(exam.json) 읽기. ID 형식부터 검사 → '../' 같은 경로 조작 차단.

    매개변수:
        exam_id: 시험 ID (예: "EX-20261013-8C7353")
    반환:
        시험 기록 dict (create_answer_sheet 반환값과 같은 형식)
        ID 형식이 틀리거나 파일이 없으면 None
    """
    if not EXAM_ID_RE.match(exam_id):          # 경로 조작 방지
        return None
    path = os.path.join(EXAM_DIR, exam_id, "exam.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def list_exams() -> list[dict]:
    """저장된 시험 기록 전체 (exam.json 목록). 만든 시각이 최근인 것부터.

    웹의 '시험 목록' 화면(GET /api/exams)이 씀. exam.json이 깨진 폴더는 건너뜀.

    매개변수:
        없음
    반환:
        시험 기록 dict 목록 (각각 get_exam() 반환값과 같은 형식). 시험이 없으면 빈 목록
    """
    out = []
    for p in glob.glob(os.path.join(EXAM_DIR, "*", "exam.json")):
        if not EXAM_ID_RE.match(os.path.basename(os.path.dirname(p))):   # 시험 ID 형식인 폴더만
            continue
        try:
            with open(p, encoding="utf-8") as f:
                out.append(json.load(f))
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(out, key=lambda r: r.get("created_at", ""), reverse=True)


def delete_exam(exam_id: str) -> bool:
    """시험 하나를 통째로 지움 (시험 폴더: exam.json, 답안지 PDF, 정답표, 올린 답안지 판독 기록·사진, 결과 엑셀).

    되돌릴 수 없음 → 웹에서는 확인 창을 거친 뒤에만 부름. 양식 좌표(templates)는 다른 시험도 쓰므로 남겨 둠.

    매개변수:
        exam_id: 시험 ID (예: "EX-20261013-8C7353")
    반환:
        지웠으면 True, ID 형식이 틀리거나 시험이 없으면 False
    """
    if not EXAM_ID_RE.match(exam_id):          # 경로 조작 방지 (get_exam과 같은 검사)
        return False
    path = os.path.join(EXAM_DIR, exam_id)
    if not os.path.isfile(os.path.join(path, "exam.json")):
        return False
    shutil.rmtree(path)
    return True


def exam_pdf_path(exam_id: str) -> str | None:
    """시험 PDF 경로. 없으면 None.

    매개변수:
        exam_id: 시험 ID (예: "EX-20261013-8C7353")
    반환:
        PDF 파일 경로 문자열 (예: ".../storage/exams/EX-20261013-8C7353/answer_sheet.pdf")
        ID 형식이 틀리거나(경로 조작 방지) 파일이 없으면 None
    """
    if not EXAM_ID_RE.match(exam_id):          # 경로 조작 방지 (get_exam과 같은 검사)
        return None
    p = os.path.join(EXAM_DIR, exam_id, "answer_sheet.pdf")
    return p if os.path.exists(p) else None
