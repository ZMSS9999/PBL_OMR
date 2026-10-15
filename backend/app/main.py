# pyright: basic
"""CheckMate 백엔드 (FastAPI) — 웹 요청을 받아 service / grading_service 함수로 넘기는 입구.

[역할]
  - 여기는 '요청 받기 → 입력 검사 → 서비스 함수 호출 → 응답 형식 맞추기'만 함
  - 실제 처리(생성·정렬·판독·채점·엑셀)는 app/service.py, app/grading_service.py, pipeline/, grading/
  - 서비스 함수가 던진 예외(ValueError 등)를 HTTP 오류 코드(404 / 409 / 422 / 500)로 바꿔 돌려줌
  - 맨 마지막에 frontend/ 폴더를 "/" 주소에 연결 → 같은 서버에서 답안지 만들기 화면도 제공

[파이프라인에서의 위치]
  준비(답안지 생성)  : POST /api/answer-sheets            → service.create_answer_sheet
  P1 정렬·P2 시험 확인·P3 판독 : POST /api/exams/{id}/sheets → grading_service.process_photo
  P4 채점            : GET /api/exams/{id}/results         → grading_service.grade_sheets
  P5 엑셀            : GET /api/exams/{id}/results.xlsx    → grading_service.export_results

[입력 → 출력]
  브라우저·React 화면이 보낸 HTTP 요청(JSON, 파일) ──► 이 파일 ──► JSON 응답 또는 파일(PDF·엑셀) 내려받기

[다른 파일과의 관계]
  - app/service.py         : 답안지 생성·시험 기록 읽기 (준비 단계)
  - app/grading_service.py : 정답표 저장, 사진 판독(P1~P3), 검수, 채점(P4), 엑셀(P5)
  - sheetgen/              : 입력 범위 숫자(NUM_QUESTIONS_RANGE 등)와 LayoutOverflowError만 가져다 씀
  - grading/               : AnswerKeyError(정답표 형식 오류)만 가져다 씀
  - frontend/index.html    : 이 서버가 그대로 내려 주는 답안지 만들기 화면

[웹에서 파이프라인이 이어지는 순서] (보통 이 순서로 호출)
  1. GET   /api/sheet-options                         입력 범위 받아서 폼 만들기
  2. POST  /api/answer-sheets                         답안지 생성 → exam_id 받기, PDF 인쇄
  3. PUT   /api/exams/{exam_id}/answer-key            정답표 입력 (또는 .../answer-key/upload 로 파일 올리기)
  4. POST  /api/exams/{exam_id}/sheets                학생 답안지 사진 올리기 (P1·P2·P3)
  5. GET   /api/exams/{exam_id}/sheets                판독 결과 목록 → needs_review 인 장 확인
  6. PATCH /api/exams/{exam_id}/sheets/{sheet_id}/review   애매한 문항·학번 검수 수정
  7. GET   /api/exams/{exam_id}/results.xlsx          채점(P4) + 엑셀(P5) 내려받기

[실행] (backend 폴더에서)
    uvicorn app.main:app --reload --port 8000
브라우저에서 http://localhost:8000 을 열면 frontend/index.html 화면이 뜸.
http://localhost:8000/docs 에서는 아래 API를 직접 눌러 시험해 볼 수 있음 (함수 docstring이 설명으로 나옴).

[API 목록]
    방식   주소                                        기능
    ------ ------------------------------------------- ------------------------------------------
    GET    /api/sheet-options                          입력 가능한 범위 (프론트엔드가 폼을 만들 때 사용)
    POST   /api/answer-sheets                          답안지 생성 → 시험 ID, PDF 주소 반환
    GET    /api/answer-sheets/{exam_id}                시험 기록 (exam.json)
    GET    /api/answer-sheets/{exam_id}/pdf            답안지 PDF (?download=true 면 내려받기)
    GET    /api/templates/{template_id}/{version}      양식 좌표 (layout.json)

    채점 (2-3 규칙 기반 판독, 2-4 채점·엑셀)
    GET    /api/exams                                  전체 시험 목록 (최근 것부터, 웹 '시험 목록' 화면)
    GET    /api/exams/{exam_id}/answer-key/template    빈 정답표 엑셀 내려받기
    PUT    /api/exams/{exam_id}/answer-key             정답표 입력 (JSON)
    POST   /api/exams/{exam_id}/answer-key/upload      정답표 파일 올리기 (.xlsx / .json)
    GET    /api/exams/{exam_id}/answer-key             저장된 정답표
    POST   /api/exams/{exam_id}/sheets                 답안지 사진 올리기 (여러 장) → 정렬·판독 (P1~P3)
    GET    /api/exams/{exam_id}/sheets                 판독한 답안지 목록 (요약)
    GET    /api/exams/{exam_id}/sheets/{sheet_id}      답안지 한 장의 판독 결과 전체
    GET    /api/exams/{exam_id}/sheets/{sheet_id}/image  정렬 이미지 (JPEG, 검수 화면의 사진)
    PATCH  /api/exams/{exam_id}/sheets/{sheet_id}/review  검수 수정 (답·학번)
    GET    /api/exams/{exam_id}/results                채점 결과 (JSON, P4)
    GET    /api/exams/{exam_id}/results.xlsx           채점 결과 엑셀 (P4 + P5)

[HTTP 오류 코드 정리]
    404 : 시험·PDF·양식·정답표·답안지를 찾을 수 없음
    409 : 정답표가 아직 없는데 채점 결과를 요청함 (순서가 맞지 않음)
    422 : 입력값 형식·범위 오류 (pydantic 검사 실패 포함)
    500 : 같은 양식 ID인데 저장된 좌표와 달라짐 (개발자가 TEMPLATE_VERSION을 올려야 하는 상황)

[주요 함수]
    _to_response         시험 기록 dict → 답안지 생성 응답 형식으로 변환
    _require_exam        없는 시험이면 404로 멈추기 (채점 API 공통 첫 줄)
    _sheet_summary       답안지 한 장 기록 → 목록용 요약 dict
    나머지 함수          위 API 표의 주소 하나당 함수 하나
"""
from __future__ import annotations

import os
import tempfile
from typing import Any, Literal

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from sheetgen import NUM_CHOICES_RANGE, NUM_QUESTIONS_RANGE, LayoutOverflowError
from grading import AnswerKeyError
from . import grading_service as gs
from . import service

# 서버 앱 객체. uvicorn이 "app.main:app" 으로 이 변수를 찾아 실행함 (title·version은 /docs 화면 제목)
app = FastAPI(title="CheckMate API", version="0.2")

# React 개발 서버(Vite 5173, CRA 3000)에서 호출할 수 있도록 허용
# (CORS: 다른 주소·포트의 웹 페이지가 이 서버를 부를 수 있게 허락하는 브라우저 보안 규칙)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000",
                   "http://127.0.0.1:5173", "http://127.0.0.1:3000"],
    allow_methods=["*"], allow_headers=["*"],
)


class AnswerSheetRequest(BaseModel):
    """POST /api/answer-sheets 요청 본문 (답안지를 만들 때 웹이 보내는 값).

    pydantic이 형식·범위를 먼저 검사함 → 범위 밖이면 함수가 실행되기 전에 422 응답.
    범위 숫자는 sheetgen/layout.py 의 NUM_QUESTIONS_RANGE, NUM_CHOICES_RANGE 한 곳에서만 정함.

    예:
        {"num_questions": 20, "num_choices": 5, "orientation": "portrait",
         "exam_name": "중간고사", "subject": "컴퓨터비전"}
    """
    # 객관식 문항 수 (필수, 정수 5~45)
    num_questions: int = Field(..., ge=NUM_QUESTIONS_RANGE[0], le=NUM_QUESTIONS_RANGE[1],
                               description="객관식 문항 수 (5~45)")
    # 문항당 선지 수 (필수, 정수 2~5. 5면 5지선다)
    num_choices: int = Field(..., ge=NUM_CHOICES_RANGE[0], le=NUM_CHOICES_RANGE[1],
                             description="선지 수 (2~5)")
    # 용지 방향: "portrait"(세로, 기본값) 또는 "landscape"(가로). 다른 값이면 422
    orientation: Literal["portrait", "landscape"] = "portrait"
    # 시험명 (선택, 30자 이하, QR에만 저장). QR 구분 문자 '|' 는 못 쓰게 정규식으로 막음
    exam_name: str = Field("", max_length=service.MAX_EXAM_NAME, pattern=r"^[^|]*$")
    # 과목명 (선택, 20자 이하, QR에만 저장). '|' 금지는 시험명과 같은 이유
    subject: str = Field("", max_length=service.MAX_SUBJECT, pattern=r"^[^|]*$")


class AnswerSheetResponse(BaseModel):
    """답안지 생성·조회 API의 응답 형식.

    exam.json 의 주요 항목 + 웹이 바로 쓸 수 있는 PDF·양식 주소 두 개.

    예:
        {"exam_id": "EX-20261001-8C7353", "template_id": "P45Q5C", "template_version": "1",
         "pdf_url": "/api/answer-sheets/EX-20261001-8C7353/pdf",
         "template_url": "/api/templates/P45Q5C/1", ...}
    """
    exam_id: str            # 시험 ID (예: "EX-20261001-8C7353")
    exam_name: str          # 시험명 (없으면 "")
    subject: str            # 과목명 (없으면 "")
    template_id: str        # 양식 ID. 방향·문항 수·선지 수로 만듦 (예: "P45Q5C" = 세로·45문항·5지선다)
    template_version: str   # 양식 버전 (예: "1"). 칸 배치 코드를 바꾸면 올라감
    orientation: str        # "portrait"(세로) / "landscape"(가로)
    num_questions: int      # 객관식 문항 수
    num_choices: int        # 선지 수
    created_at: str         # 생성 시각, 한국 시간 ISO 형식 (예: "2026-10-01T14:03:22+09:00")
    pdf_url: str            # 답안지 PDF 주소 (뒤에 ?download=true 붙이면 내려받기)
    template_url: str       # 양식 좌표 JSON 주소


def _to_response(rec: dict) -> AnswerSheetResponse:
    """시험 기록(exam.json 내용) → 답안지 응답 형식으로 변환.

    exam.json 에는 응답에 없는 항목(qr_payload 등)도 있어서, 응답 형식에 있는 키만 골라 담음.
    pdf_url, template_url 은 저장된 값이 아니라 여기서 주소 문자열로 새로 만듦.

    매개변수:
        rec: service.create_answer_sheet / service.get_exam 이 돌려준 시험 기록 dict
             (예: {"exam_id": "EX-20261001-8C7353", "template_id": "P45Q5C", ...})
    반환:
        AnswerSheetResponse 객체 (FastAPI가 JSON으로 바꿔 보냄)
    """
    return AnswerSheetResponse(
        # 응답 형식에 정의된 항목 이름(model_fields) 중 rec 에 있는 것만 복사
        **{k: rec[k] for k in AnswerSheetResponse.model_fields if k in rec},
        pdf_url=f"/api/answer-sheets/{rec['exam_id']}/pdf",
        template_url=f"/api/templates/{rec['template_id']}/{rec['template_version']}",
    )


@app.get("/api/sheet-options")
def sheet_options():
    """입력 가능한 범위·기본값 알려 주기 (GET /api/sheet-options).

    프론트엔드가 슬라이더 최소·최대, 선지 버튼 개수, 글자 수 제한을 이 값으로 만듦
    → 범위를 바꿀 때 layout.py 숫자만 고치면 화면도 같이 바뀜.

    매개변수:
        없음
    반환:
        dict. 예:
        {"num_questions": {"min": 5, "max": 45, "step": 1, "default": 20},
         "num_choices": {"min": 2, "max": 5, "step": 1, "default": 5},
         "orientations": [{"value": "portrait", "label": "세로"}, {"value": "landscape", "label": "가로"}],
         "short_answers": 5,
         "max_length": {"exam_name": 30, "subject": 20}}
    """
    return {
        "num_questions": {"min": NUM_QUESTIONS_RANGE[0], "max": NUM_QUESTIONS_RANGE[1], "step": 1, "default": 20},
        "num_choices": {"min": NUM_CHOICES_RANGE[0], "max": NUM_CHOICES_RANGE[1], "step": 1, "default": 5},
        "orientations": [{"value": "portrait", "label": "세로"}, {"value": "landscape", "label": "가로"}],
        "short_answers": 5,     # 주관식 문항 수 (회의 결정으로 5문항 고정)
        "max_length": {"exam_name": service.MAX_EXAM_NAME, "subject": service.MAX_SUBJECT},
    }


@app.post("/api/answer-sheets", response_model=AnswerSheetResponse, status_code=201)
def create_answer_sheet(req: AnswerSheetRequest):
    """답안지 생성 (POST /api/answer-sheets) — 파이프라인 준비 단계.

    양식 좌표 계산·저장 → 시험 ID 발급 → QR 포함 PDF 생성 → exam.json 저장까지
    service.create_answer_sheet 가 처리하고, 여기서는 결과를 응답 형식으로 바꾸기만 함.

    매개변수:
        req: 요청 본문 (AnswerSheetRequest. 문항 수·선지 수·방향·시험명·과목명)
    반환:
        AnswerSheetResponse (상태 코드 201 = 새로 만들어짐)
    예외:
        HTTP 422: 입력값이 잘못됐거나(ValueError) 칸이 용지에 다 안 들어감(LayoutOverflowError)
        HTTP 500: 같은 양식 ID·버전인데 저장된 좌표와 다름(TemplateConflictError, 개발자 확인 필요)
    """
    try:
        rec = service.create_answer_sheet(**req.model_dump())
    except (ValueError, LayoutOverflowError) as e:
        raise HTTPException(status_code=422, detail=str(e))
    except service.TemplateConflictError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return _to_response(rec)


@app.get("/api/answer-sheets/{exam_id}", response_model=AnswerSheetResponse)
def get_answer_sheet(exam_id: str):
    """시험 기록 조회 (GET /api/answer-sheets/{exam_id}).

    매개변수:
        exam_id: 시험 ID (예: "EX-20261001-8C7353")
    반환:
        AnswerSheetResponse (생성할 때와 같은 형식)
    예외:
        HTTP 404: 시험이 없거나 ID 형식이 틀림
    """
    rec = service.get_exam(exam_id)
    if not rec:
        raise HTTPException(404, "시험을 찾을 수 없습니다")
    return _to_response(rec)


@app.get("/api/answer-sheets/{exam_id}/pdf")
def get_answer_sheet_pdf(exam_id: str, download: bool = False):
    """답안지 PDF 파일 보내기 (GET /api/answer-sheets/{exam_id}/pdf).

    download 값에 따라 브라우저 동작이 달라짐: 파일 이름을 붙이면 '내려받기', 안 붙이면 화면에 바로 표시
    (frontend 미리보기 iframe 은 download 없이 부름).

    매개변수:
        exam_id: 시험 ID (예: "EX-20261001-8C7353")
        download: True면 "<exam_id>.pdf" 이름으로 내려받기 (주소 뒤 ?download=true), 기본 False
    반환:
        PDF 파일 응답 (application/pdf)
    예외:
        HTTP 404: PDF가 없거나 ID 형식이 틀림
    """
    path = service.exam_pdf_path(exam_id)
    if not path:
        raise HTTPException(404, "PDF를 찾을 수 없습니다")
    return FileResponse(path, media_type="application/pdf",
                        filename=f"{exam_id}.pdf" if download else None)


@app.get("/api/templates/{template_id}/{version}")
def get_template(template_id: str, version: str):
    """양식 좌표 JSON 보내기 (GET /api/templates/{template_id}/{version}).

    template_id·version 이 영문·숫자로만 되어 있을 때만 파일을 찾음
    → "../" 같은 문자로 다른 폴더 파일을 읽으려는 경로 조작을 막기 위함.

    매개변수:
        template_id: 양식 ID (예: "P45Q5C")
        version: 양식 버전 (예: "1")
    반환:
        layout dict (칸 좌표 전체. 예: {"template_id": "P45Q5C", "frame": {...}, "mcq": {...}, ...})
    예외:
        HTTP 404: 양식 파일이 없거나 ID·버전에 영문·숫자 외 문자가 있음
    """
    lay = service.load_template(template_id, version) if template_id.isalnum() and version.isalnum() else None
    if not lay:
        raise HTTPException(404, "양식을 찾을 수 없습니다")
    return lay


# ---------------- 채점: 정답표 → 사진 판독 → 채점 → 엑셀 ----------------
# 엑셀(.xlsx) 파일의 MIME 형식 이름. 파일 응답에 붙이면 브라우저가 엑셀 파일로 인식함
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# 사진 한 장 업로드 최대 크기 (20MB). 넘으면 그 장만 "rejected" 처리
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


class AnswerKeyRequest(BaseModel):
    """PUT /api/exams/{exam_id}/answer-key 요청 본문 (정답표 입력).

    문항 수·선지 수는 보내지 않음 → 시험 기록에서 가져와서 입력 실수를 막음.
    JSON 키는 문자열만 되므로 문항 번호도 "1", "2" 처럼 문자열로 보냄.

    예:
        {"answers": {"1": 3, "2": [1, 4]}, "points": {"5": 2}, "default_points": 1}
        → 1번 정답 3, 2번 정답 1 또는 4(복수 정답), 5번만 2점, 나머지 1점
    """
    # 문항별 정답 {문항 번호: 정답}. 정답은 3 / [1, 4] / "1,4" 같은 형식 허용
    answers: dict[str, Any] = Field(..., description='문항별 정답. 예: {"1": 3, "2": [1, 4]}')
    # 문항별 배점 {문항 번호: 점수}. 적지 않은 문항은 default_points 사용
    points: dict[str, float] = Field(default_factory=dict, description="문항별 배점 (없으면 default_points)")
    # 기본 배점 (0 이상, 기본 1점)
    default_points: float = Field(1, ge=0)


class ReviewRequest(BaseModel):
    """PATCH .../sheets/{sheet_id}/review 요청 본문 (검수 수정).

    선생님이 사진을 보고 확정한 답·학번만 보냄. 보내지 않은 문항은 판독 결과 그대로 둠.

    예:
        {"corrections": {"7": 2, "9": []}, "student_id": "2026123456"}
        → 7번 답은 2, 9번은 무응답으로 확정, 학번은 2026123456 으로 확정
    """
    # 문항별 확정 답 {문항 번호: 답}. [] 는 무응답, [1, 4] 는 복수 응답
    corrections: dict[str, Any] = Field(default_factory=dict, description='검수로 확정한 답. 예: {"7": 2, "9": []}')
    # 확정 학번 (숫자 문자열). None 이면 학번은 고치지 않음
    student_id: str | None = None


def _require_exam(exam_id: str) -> dict[str, Any]:
    """시험이 있는지 확인. 없으면 바로 404로 요청을 끝냄 (채점 API 함수들의 공통 첫 줄).

    매개변수:
        exam_id: 시험 ID (예: "EX-20261001-8C7353")
    반환:
        시험 기록 dict (exam.json 내용)
    예외:
        HTTP 404: 시험이 없거나 ID 형식이 틀림
    """
    rec = service.get_exam(exam_id)
    if not rec:
        raise HTTPException(404, "시험을 찾을 수 없습니다")
    return rec


def _sheet_summary(rec: dict[str, Any]) -> dict[str, Any]:
    """답안지 한 장의 저장 기록 → 목록 화면용 요약.

    전체 기록에는 칸별 채움 비율(fill)까지 들어 있어 큼 → 목록에는 상태·학번·문항별 답만 보냄.
    판독에 성공한 장(status "read")만 학번·답 항목이 추가됨.

    매개변수:
        rec: grading_service.process_photo / get_sheet 가 돌려준 답안지 기록 dict
    반환:
        dict. 판독 성공 예:
        {"sheet_id": "S-1A2B3C4D", "source": "photo1.jpg", "status": "read", "error": None,
         "processed_at": "2026-10-01T15:00:00+09:00", "notes": [],
         "student_id": "2026123456", "student_id_raw": "2026123456",
         "status_counts": {"ok": 18, "ambiguous": 2}, "needs_review": True,
         "answers": {1: [3], 2: None, ...}}
        정렬 실패 등은 앞의 6개 항목만 있음 (status "align_failed", error 에 사유)
    """
    out = {k: rec.get(k) for k in ("sheet_id", "source", "status", "error", "processed_at", "notes")}
    if rec["status"] == "read":
        read = rec["read"]
        # P3 판독 결과에서 학번(완성본·읽은 그대로), 상태별 문항 수, 검수 필요 여부, 문항별 답만 꺼냄
        out.update(student_id=read["student_id"]["text"], student_id_raw=read["student_id"]["raw"],
                   status_counts=read["summary"]["status_counts"], needs_review=read["summary"]["needs_review"],
                   answers={q["q"]: q["answer"] for q in read["mcq"]})
    return out


@app.get("/api/exams/{exam_id}/answer-key/template")
def answer_key_template(exam_id: str):
    """빈 정답표 엑셀 내려받기 (GET /api/exams/{exam_id}/answer-key/template).

    이 시험의 문항 수·선지 수에 맞춘 엑셀을 만들어 보냄 → 선생님이 '정답'·'배점' 칸을 채워 다시 올림.

    매개변수:
        exam_id: 시험 ID (예: "EX-20261001-8C7353")
    반환:
        엑셀 파일 응답 (파일 이름 "<exam_id>_정답표.xlsx")
    예외:
        HTTP 404: 시험이 없음
    """
    _require_exam(exam_id)
    return FileResponse(gs.make_answer_key_template(exam_id), media_type=XLSX,
                        filename=f"{exam_id}_정답표.xlsx")


@app.put("/api/exams/{exam_id}/answer-key")
def put_answer_key(exam_id: str, req: AnswerKeyRequest):
    """정답표 입력 (PUT /api/exams/{exam_id}/answer-key) — P4 채점의 기준이 되는 값.

    이미 정답표가 있으면 새 값으로 덮어씀 (PUT = 통째로 교체).

    매개변수:
        exam_id: 시험 ID (예: "EX-20261001-8C7353")
        req: 요청 본문 (AnswerKeyRequest. 정답·배점)
    반환:
        저장된 정답표 dict. 예:
        {"exam_id": "EX-...", "num_questions": 20, "num_choices": 5,
         "answers": {"1": [3], "2": [1, 4], ...}, ...}
    예외:
        HTTP 404: 시험이 없음
        HTTP 422: 정답이 선지 범위를 벗어나는 등 정답표 형식 오류(AnswerKeyError)
    """
    _require_exam(exam_id)
    try:
        key = gs.set_answer_key(exam_id, req.answers, req.points, req.default_points)
    except AnswerKeyError as e:
        raise HTTPException(422, str(e))
    return key.to_dict()


@app.post("/api/exams/{exam_id}/answer-key/upload")
async def upload_answer_key(exam_id: str, file: UploadFile = File(...)):
    """정답표 파일 올리기 (POST /api/exams/{exam_id}/answer-key/upload).

    읽기 함수(load_answer_key)가 파일 경로를 받으므로, 올라온 내용을 임시 폴더에 파일로 잠깐 썼다가 읽음.
    임시 폴더는 with 블록이 끝나면 자동으로 지워짐.

    매개변수:
        exam_id: 시험 ID (예: "EX-20261001-8C7353")
        file: 올린 파일 (.xlsx 또는 .json. 대소문자 구분 없음)
    반환:
        저장된 정답표 dict (PUT answer-key 응답과 같은 형식)
    예외:
        HTTP 404: 시험이 없음
        HTTP 422: 확장자가 .xlsx/.json 이 아님, 정답표 형식 오류,
                  문항 수·선지 수가 시험과 다름 (다른 시험 정답표를 잘못 올린 경우)
    """
    _require_exam(exam_id)
    # 파일 이름 끝(확장자)을 보고 ".xlsx" / ".json" / None 중 하나로 정함
    name = (file.filename or "").lower()
    suffix = ".xlsx" if name.endswith(".xlsx") else ".json" if name.endswith(".json") else None
    if not suffix:
        raise HTTPException(422, "정답표는 .xlsx 또는 .json 파일이어야 합니다")
    data = await file.read()
    # 임시 폴더에 "key.xlsx" 또는 "key.json" 으로 저장 → 확장자로 읽는 방식을 고름
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "key" + suffix)
        with open(p, "wb") as f:
            f.write(data)
        try:
            key = gs.import_answer_key(exam_id, p)
        except (AnswerKeyError, ValueError) as e:
            raise HTTPException(422, str(e))
    return key.to_dict()


@app.get("/api/exams/{exam_id}/answer-key")
def get_answer_key(exam_id: str):
    """저장된 정답표 조회 (GET /api/exams/{exam_id}/answer-key).

    매개변수:
        exam_id: 시험 ID (예: "EX-20261001-8C7353")
    반환:
        정답표 dict (PUT answer-key 응답과 같은 형식)
    예외:
        HTTP 404: 시험이 없거나 정답표를 아직 입력하지 않음
    """
    _require_exam(exam_id)
    key = gs.get_answer_key(exam_id)
    if not key:
        raise HTTPException(404, "정답표가 아직 없습니다")
    return key.to_dict()


@app.post("/api/exams/{exam_id}/sheets", status_code=201)
async def upload_sheets(exam_id: str, files: list[UploadFile] = File(...)):
    """답안지 사진 여러 장 → 정렬 → QR 확인 → 규칙 기반 판독. 실패한 장도 사유와 함께 기록.

    (POST /api/exams/{exam_id}/sheets) 파이프라인 P1 정렬 → P2 시험 확인 → P3 판독.
    한 장씩 차례로 처리하고, 어떤 장이 실패해도 나머지 장은 계속 처리함.
    채점(P4)은 여기서 하지 않음 → 결과를 요청할 때 최신 정답표로 채점.

    장마다 결과 status:
        "rejected"     : 20MB 초과이거나 이미지 파일이 아님 (저장 안 함)
        "align_failed" : P1 정렬 실패 → 재촬영 필요 (저장함)
        "wrong_exam"   : P2 다른 시험 답안지 (저장 안 함)
        "read"         : P3 판독까지 성공 (저장함)

    매개변수:
        exam_id: 시험 ID (예: "EX-20261001-8C7353")
        files: 올린 사진 파일 목록 (jpg/png, 여러 장)
    반환:
        {"exam_id": "EX-...", "sheets": [장별 요약 dict, ...]}
        장별 요약은 _sheet_summary 형식, rejected 는 {"source", "status", "error"} 만 있음
    예외:
        HTTP 404: 시험이 없음
    """
    _require_exam(exam_id)
    results = []
    for uf in files:
        data = await uf.read()
        # 너무 큰 파일은 디코딩 전에 거르기 (서버 메모리 보호)
        if len(data) > MAX_UPLOAD_BYTES:
            results.append({"source": uf.filename, "status": "rejected", "error": "파일이 20MB를 넘습니다"})
            continue
        # 바이트 → 이미지 배열. 이미지가 아니면 이 장만 rejected 로 넘김
        try:
            img = gs.decode_image(data)
        except ValueError as e:
            results.append({"source": uf.filename, "status": "rejected", "error": str(e)})
            continue
        # P1·P2·P3 처리 + 저장 → 목록용 요약으로 바꿔 담기
        results.append(_sheet_summary(gs.process_photo(exam_id, img, uf.filename or "")))
    return {"exam_id": exam_id, "sheets": results}


@app.get("/api/exams", response_model=list[AnswerSheetResponse])
def list_exams():
    """전체 시험 목록 (GET /api/exams). 웹의 '시험 목록' 화면용.

    매개변수:
        없음
    반환:
        AnswerSheetResponse 목록 (만든 시각이 최근인 것부터). 시험이 없으면 빈 목록
    """
    return [_to_response(r) for r in service.list_exams()]


@app.delete("/api/exams/{exam_id}", status_code=204)
def delete_exam(exam_id: str):
    """시험 삭제 (DELETE /api/exams/{exam_id}). 시험 폴더 전체(답안지 PDF·정답표·판독 기록·사진·결과)를 지움.

    매개변수:
        exam_id: 시험 ID (예: "EX-20261001-8C7353")
    반환:
        내용 없음 (204)
    예외:
        HTTP 404: 시험이 없거나 ID 형식이 틀림
    """
    if not service.delete_exam(exam_id):
        raise HTTPException(404, "시험을 찾을 수 없습니다")


@app.get("/api/exams/{exam_id}/sheets")
def list_sheets(exam_id: str):
    """판독한 답안지 목록 (GET /api/exams/{exam_id}/sheets).

    저장된 답안지(판독 성공 + 정렬 실패)를 처리 시각 순으로 요약해서 보냄.
    검수 화면에서 needs_review 가 True 인 장을 골라 볼 때 사용.

    매개변수:
        exam_id: 시험 ID (예: "EX-20261001-8C7353")
    반환:
        _sheet_summary 형식 dict 의 목록 (예: [{"sheet_id": "S-1A2B3C4D", "status": "read", ...}, ...])
    예외:
        HTTP 404: 시험이 없음
    """
    _require_exam(exam_id)
    return [_sheet_summary(r) for r in gs.list_sheets(exam_id)]


@app.get("/api/exams/{exam_id}/sheets/{sheet_id}")
def get_sheet(exam_id: str, sheet_id: str):
    """답안지 한 장의 판독 결과 전체 (GET /api/exams/{exam_id}/sheets/{sheet_id}).

    요약과 달리 칸별 채움 비율(fill), 정렬 정보(align), 검수 기록(review)까지 모두 보냄 → 검수 화면용.

    매개변수:
        exam_id: 시험 ID (예: "EX-20261001-8C7353")
        sheet_id: 답안지 ID (예: "S-1A2B3C4D")
    반환:
        저장된 답안지 기록 dict 전체 (grading_service 의 sheets/<sheet_id>.json 내용)
    예외:
        HTTP 404: 시험 또는 답안지가 없음 (ID 형식이 틀린 경우 포함)
    """
    _require_exam(exam_id)
    rec = gs.get_sheet(exam_id, sheet_id)
    if not rec:
        raise HTTPException(404, "답안지를 찾을 수 없습니다")
    return rec


@app.get("/api/exams/{exam_id}/sheets/{sheet_id}/image")
def get_sheet_image(exam_id: str, sheet_id: str):
    """답안지 한 장의 정렬 이미지 (GET /api/exams/{exam_id}/sheets/{sheet_id}/image). 검수 화면의 사진.

    P1 정렬 결과(200dpi 정면 이미지 JPEG)를 그대로 보냄 → 웹이 layout 좌표로 칸 위치를 겹쳐 그릴 수 있음.

    매개변수:
        exam_id: 시험 ID (예: "EX-20261001-8C7353")
        sheet_id: 답안지 ID (예: "S-1A2B3C4D")
    반환:
        JPEG 파일 응답 (image/jpeg)
    예외:
        HTTP 404: 시험·답안지가 없거나 정렬 실패로 이미지가 없음
    """
    _require_exam(exam_id)
    path = gs.aligned_image_path(exam_id, sheet_id)
    if not path:
        raise HTTPException(404, "정렬 이미지가 없습니다")
    return FileResponse(path, media_type="image/jpeg")


@app.patch("/api/exams/{exam_id}/sheets/{sheet_id}/review")
def review_sheet(exam_id: str, sheet_id: str, req: ReviewRequest):
    """검수 수정 (PATCH /api/exams/{exam_id}/sheets/{sheet_id}/review).

    선생님이 확정한 답·학번을 답안지 기록의 review 칸에 저장함 (판독 원본 read 는 그대로 둠).
    PATCH = 보낸 문항만 고치고 나머지는 유지. 다음 채점(P4) 때부터 이 값이 반영됨.

    매개변수:
        exam_id: 시험 ID (예: "EX-20261001-8C7353")
        sheet_id: 답안지 ID (예: "S-1A2B3C4D")
        req: 요청 본문 (ReviewRequest. 문항별 확정 답, 확정 학번)
    반환:
        _sheet_summary 형식 + "review" 항목. 예:
        {..., "review": {"corrections": {"7": [2]}, "student_id": "2026123456",
                         "updated_at": "2026-10-01T15:10:00+09:00"}}
    예외:
        HTTP 404: 시험이 없거나, 답안지가 없거나, 판독에 성공한 답안지가 아님(LookupError)
        HTTP 422: 학번에 숫자 외 문자가 있는 등 입력값 오류(ValueError)
    """
    _require_exam(exam_id)
    try:
        rec = gs.review_sheet(exam_id, sheet_id, req.corrections, req.student_id)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    # 두 dict 합치기 (|) → 요약 + 검수 기록
    return _sheet_summary(rec) | {"review": rec["review"]}


@app.get("/api/exams/{exam_id}/results")
def get_results(exam_id: str):
    """채점 결과 JSON (GET /api/exams/{exam_id}/results) — 파이프라인 P4.

    요청할 때마다 저장된 판독 결과 + 최신 정답표 + 검수 수정값으로 다시 채점함.

    매개변수:
        exam_id: 시험 ID (예: "EX-20261001-8C7353")
    반환:
        답안지별 채점 결과 dict 의 목록 (용량이 큰 판독 원본 "read" 항목은 뺌). 예:
        [{"sheet_id": "S-1A2B3C4D", "source": "photo1.jpg", "status": "확정",
          "student_id": "2026123456", "score": 17.0, "items": [...], ...}, ...]
    예외:
        HTTP 404: 시험이 없음
        HTTP 409: 정답표가 아직 없음 (정답표 입력부터 해야 함)
    """
    _require_exam(exam_id)
    try:
        records = gs.grade_sheets(exam_id)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return [{k: v for k, v in r.items() if k != "read"} for r in records]


@app.get("/api/exams/{exam_id}/results.xlsx")
def get_results_xlsx(exam_id: str):
    """채점 결과 엑셀 내려받기 (GET /api/exams/{exam_id}/results.xlsx) — 파이프라인 P4 + P5.

    다시 채점(P4)한 뒤 엑셀(P5)을 시험 폴더의 results.xlsx 로 저장하고 그 파일을 보냄.
    엑셀 시트: 학생별 점수표 / 문항별 결과 / 문항별 정답률 / 검수 대기 / 정답표.

    매개변수:
        exam_id: 시험 ID (예: "EX-20261001-8C7353")
    반환:
        엑셀 파일 응답 (파일 이름 "<exam_id>_채점결과.xlsx")
    예외:
        HTTP 404: 시험이 없음
        HTTP 409: 정답표가 아직 없음
    """
    _require_exam(exam_id)
    try:
        path = gs.export_results(exam_id)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return FileResponse(path, media_type=XLSX, filename=f"{exam_id}_채점결과.xlsx")


# 맨 마지막에: 나머지 경로는 정적 프론트엔드 (React 빌드 결과로 바꿔 끼워도 됨)
# "/" 에 붙이는 연결은 위의 /api/... 주소보다 뒤에 와야 API 요청을 가로채지 않음
# html=True → "/" 로 접속하면 frontend/index.html 을 보여 줌. frontend 폴더가 없으면 연결 생략
FRONTEND_DIR = os.path.join(os.path.dirname(service.BASE_DIR), "frontend")   # checkmate/frontend 폴더 경로
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
