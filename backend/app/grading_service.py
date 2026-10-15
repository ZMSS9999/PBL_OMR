# pyright: basic
"""채점 서비스 — 파이프라인(pipeline/flow.py) 결과를 storage/에 저장하고 다시 불러오는 층.

[역할]
  정답표 저장 → 사진 판독(P1·P2·P3) 결과 저장 → 검수 수정 → 채점(P4) → 엑셀(P5)
  각 단계의 실제 계산은 pipeline/flow.py, grading/ 가 하고, 이 파일은 그 결과를 파일로 남기고 다시 읽는 일을 함

[파이프라인에서의 위치]
  P1 정렬 · P2 시험 확인 · P3 판독 : process_photo()   (사진 올릴 때)
  P4 채점                          : grade_sheets()    (결과를 요청할 때)
  P5 엑셀                          : export_results()  (엑셀을 요청할 때)

[입력 → 출력]
  정답표(JSON/엑셀)        ──set_answer_key / import_answer_key──► answer_key.json
  답안지 사진(바이트)      ──decode_image → process_photo──────► sheets/<sheet_id>.json + _aligned.jpg
  검수 수정값              ──review_sheet─────────────────────► sheets/<sheet_id>.json 의 review 칸
  저장된 판독 결과 + 정답표 ──grade_sheets → export_results───► results.xlsx

[왜 따로 있나]
  - flow.py   : 단계 잇기만 함 (메모리 안, 저장 없음)
  - 이 파일   : 웹은 요청이 한 번씩 따로 옴 (정답표 입력 → 사진 업로드 → 검수 → 엑셀 요청)
               → 단계 사이 결과를 파일로 남겨 두고 다음 요청에서 이어 받아야 함
  - 웹(app/main.py)과 명령행(tools/grade_photos.py)이 이 파일의 같은 함수를 부름

[다른 파일과의 관계]
  - 부르는 쪽: app/main.py (웹 API), tools/grade_photos.py, tools/demo_end_to_end.py (명령행)
  - 부르는 대상:
      app/service.py      : 시험 기록(exam.json)·양식 좌표(layout.json) 읽기, 저장 폴더 경로
      pipeline/flow.py    : run_photo(P1~P3), step_grade(P4), step_export(P5), 상태 값 READ / WRONG_EXAM
      grading/            : AnswerKey(정답표), 정답표 파일 읽기·쓰기, parse_choices(답 표기 해석),
                            retake_record(재촬영 필요 행)

[저장 구조] (service.py의 시험 폴더 아래)
  storage/exams/<exam_id>/answer_key.json               정답표·배점 (P4의 입력)
  storage/exams/<exam_id>/answer_key_template.xlsx      선생님이 채울 빈 정답표
  storage/exams/<exam_id>/sheets/<sheet_id>.json        사진 1장의 판독 결과 (P3 출력) + 검수 수정
  storage/exams/<exam_id>/sheets/<sheet_id>_aligned.jpg 정렬 이미지 (P1 출력, 검수 화면용)
  storage/exams/<exam_id>/results.xlsx                  채점 결과 엑셀 (P5 출력)

  sheets/<sheet_id>.json 내용 (process_photo가 만드는 rec):
    sheet_id      답안지 ID (예: "S-1A2B3C4D")
    exam_id       시험 ID
    source        원래 파일 이름 (예: "photo1.jpg")
    processed_at  처리 시각 (한국 시간)
    status        "read"(판독 성공) / "align_failed"(정렬 실패 → 재촬영 필요)
    error         실패 사유 (성공이면 None)
    notes         참고 메모 목록 (예: "QR을 읽지 못해 선택한 시험의 양식으로 판독함")
    align         정렬 요약 (방향, 찾은 마커 ID, 재투영 오차, 흐림 정도, QR 읽기 성공 여부)
    read          P3 판독 결과 (문항별 답·칸별 채움 비율·학번). 검수 후에도 바꾸지 않음
    review        검수 기록 {"corrections": {문항: 답}, "student_id": 학번, "updated_at": 시각}

[채점(P4)을 업로드 때 하지 않고 엑셀 만들 때 하는 이유]
  정답표가 나중에 바뀌거나 검수에서 답이 고쳐질 수 있음
  → 판독 결과(P3)만 저장해 두고, 결과를 요청할 때마다 최신 정답표·검수값으로 다시 채점함

[주요 함수]
  answer_key_path()           정답표 JSON 경로
  make_answer_key_template()  빈 정답표 엑셀 만들기
  set_answer_key()            웹 JSON 정답표 저장
  import_answer_key()         정답표 파일(.xlsx/.json) 읽어서 저장
  get_answer_key()            저장된 정답표 읽기
  decode_image()              업로드 바이트 → 이미지 배열
  process_photo()             사진 1장 P1·P2·P3 → 저장
  list_sheets()               저장된 답안지 기록 전체
  get_sheet()                 답안지 한 장 기록
  review_sheet()              검수 수정 저장
  grade_sheets()              P4 채점 (답안지 전체)
  export_results()            P4 + P5 엑셀 파일 만들기

[사용 예] (backend 폴더에서)
    from app import grading_service as gs
    gs.set_answer_key("EX-20261013-8C7353", {"1": 3, "2": [1, 4]})
    rec = gs.process_photo("EX-20261013-8C7353", gs.decode_image(open("p1.jpg", "rb").read()), "p1.jpg")
    gs.review_sheet("EX-20261013-8C7353", rec["sheet_id"], {"7": 2})
    gs.export_results("EX-20261013-8C7353")      # → ".../results.xlsx"
"""
from __future__ import annotations

import json
import os
import re
import secrets
from datetime import datetime
from typing import Any

import cv2
import numpy as np

from grading import AnswerKey, answer_key_template, load_answer_key, parse_choices, save_answer_key
from grading.score import retake_record
from pipeline import flow
from pipeline.read import ReadConfig
from . import service
from .service import KST

SHEET_ID_RE = re.compile(r"^S-[0-9A-F]{8}$")    # 답안지 ID 형식 (경로 조작 방지용 검사)


class ExamNotFoundError(LookupError):
    """시험 기록(exam.json)이 없거나 시험 ID 형식이 틀릴 때 발생.

    LookupError를 물려받았으므로 LookupError로 잡아도 함께 잡힘.
    """
    pass


def _exam_dir(exam_id: str) -> str:
    """시험 폴더 경로. 없는 시험이면 ExamNotFoundError (service.get_exam이 ID 형식도 검사함).

    매개변수:
        exam_id: 시험 ID (예: "EX-20261013-8C7353")
    반환:
        시험 폴더 경로 문자열 (예: ".../storage/exams/EX-20261013-8C7353")
    예외:
        ExamNotFoundError: 시험이 없거나 ID 형식이 틀림
    """
    if not service.get_exam(exam_id):
        raise ExamNotFoundError(f"시험 {exam_id}을(를) 찾을 수 없습니다")
    return os.path.join(service.EXAM_DIR, exam_id)


def _sheets_dir(exam_id: str) -> str:
    """답안지 기록을 저장하는 sheets 폴더 경로. 폴더가 없으면 새로 만듦.

    매개변수:
        exam_id: 시험 ID (예: "EX-20261013-8C7353")
    반환:
        폴더 경로 문자열 (예: ".../storage/exams/EX-20261013-8C7353/sheets")
    예외:
        ExamNotFoundError: 시험이 없음 (_exam_dir에서 발생)
    """
    d = os.path.join(_exam_dir(exam_id), "sheets")
    os.makedirs(d, exist_ok=True)
    return d


def _exam_and_layout(exam_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """시험 기록(exam.json) + 그 시험이 쓰는 양식 좌표(layout.json). 파이프라인 P2·P3의 입력.

    매개변수:
        exam_id: 시험 ID (예: "EX-20261013-8C7353")
    반환:
        (exam, layout) 튜플
            exam   : 시험 기록 dict (예: {"exam_id": ..., "num_questions": 20, "num_choices": 5, ...})
            layout : 양식 좌표 dict (예: {"template_id": "P20Q5C", "orientation": "portrait", ...})
    예외:
        ExamNotFoundError: 시험이 없음
        RuntimeError: 시험 기록은 있는데 양식 파일이 없음 (저장 폴더가 일부 지워진 경우 등)
    """
    exam = service.get_exam(exam_id)
    if not exam:
        raise ExamNotFoundError(f"시험 {exam_id}을(를) 찾을 수 없습니다")
    layout = service.load_template(exam["template_id"], exam["template_version"])
    if layout is None:
        raise RuntimeError(f"양식 {exam['template_id']} v{exam['template_version']} 파일이 없습니다")
    return exam, layout


# ---------------- 정답표 (P4의 입력) ----------------
def answer_key_path(exam_id: str) -> str:
    """이 시험의 정답표 JSON 파일 경로 (파일이 있는지는 확인하지 않음).

    매개변수:
        exam_id: 시험 ID (예: "EX-20261013-8C7353")
    반환:
        경로 문자열 (예: ".../storage/exams/EX-20261013-8C7353/answer_key.json")
    예외:
        ExamNotFoundError: 시험이 없음
    """
    return os.path.join(_exam_dir(exam_id), "answer_key.json")


def make_answer_key_template(exam_id: str) -> str:
    """이 시험의 문항 수·선지 수에 맞는 빈 정답표 엑셀 생성 → 경로 반환.

    요청할 때마다 새로 만들어 같은 경로에 덮어씀.

    매개변수:
        exam_id: 시험 ID (예: "EX-20261013-8C7353")
    반환:
        엑셀 파일 경로 (예: ".../storage/exams/EX-20261013-8C7353/answer_key_template.xlsx")
    예외:
        ExamNotFoundError: 시험이 없음
        RuntimeError: 양식 파일이 없음
    """
    exam, _ = _exam_and_layout(exam_id)
    path = os.path.join(_exam_dir(exam_id), "answer_key_template.xlsx")
    return answer_key_template(path, exam["num_questions"], exam["num_choices"], exam_id)


def set_answer_key(exam_id: str, answers: dict[Any, Any], points: dict[Any, Any] | None = None,
                   default_points: float = 1) -> AnswerKey:
    """웹에서 JSON으로 받은 정답표 저장. 문항 수·선지 수는 시험 기록에서 가져옴 (입력 실수 방지).

    이미 정답표가 있으면 덮어씀.

    매개변수:
        exam_id: 시험 ID (예: "EX-20261013-8C7353")
        answers: 문항별 정답 (예: {"1": 3, "2": [1, 4]} → 2번은 1 또는 4 인정)
        points: 문항별 배점 (예: {"5": 2}). None 또는 생략이면 모두 default_points
        default_points: 배점을 따로 적지 않은 문항의 점수 (기본 1)
    반환:
        저장된 AnswerKey 객체
    예외:
        ExamNotFoundError: 시험이 없음
        AnswerKeyError: 정답이 비어 있거나 선지 범위 밖인 등 정답표 내용 오류
    """
    exam, _ = _exam_and_layout(exam_id)
    key = AnswerKey.from_dict({"exam_id": exam_id, "num_questions": exam["num_questions"],
                               "num_choices": exam["num_choices"], "answers": answers,
                               "points": points or {}, "default_points": default_points})
    save_answer_key(key, answer_key_path(exam_id))
    return key


def import_answer_key(exam_id: str, path: str) -> AnswerKey:
    """엑셀(.xlsx) 또는 JSON 정답표 파일을 읽어 이 시험의 정답표로 저장.
    문항 수·선지 수가 시험과 다르면 거부 (다른 시험 정답표를 잘못 올린 경우 방지).

    파일 안의 시험 ID와 상관없이 이 시험(exam_id)의 정답표로 저장함.

    매개변수:
        exam_id: 시험 ID (예: "EX-20261013-8C7353")
        path: 정답표 파일 경로 (확장자 .xlsx 또는 .json 으로 읽는 방식 구분)
    반환:
        저장된 AnswerKey 객체
    예외:
        ExamNotFoundError: 시험이 없음
        AnswerKeyError: 정답표 내용·형식 오류
        ValueError: 문항 수·선지 수가 시험과 다름
    """
    exam, _ = _exam_and_layout(exam_id)
    key = load_answer_key(path, num_choices=exam["num_choices"], exam_id=exam_id)
    if key.num_questions != exam["num_questions"] or key.num_choices != exam["num_choices"]:
        raise ValueError(f"정답표({key.num_questions}문항·{key.num_choices}지)가 시험"
                         f"({exam['num_questions']}문항·{exam['num_choices']}지)과 다릅니다")
    key.exam_id = exam_id
    save_answer_key(key, answer_key_path(exam_id))
    return key


def get_answer_key(exam_id: str) -> AnswerKey | None:
    """저장된 정답표 읽기.

    매개변수:
        exam_id: 시험 ID (예: "EX-20261013-8C7353")
    반환:
        AnswerKey 객체. 아직 정답표를 입력하지 않았으면 None
    예외:
        ExamNotFoundError: 시험이 없음
    """
    p = answer_key_path(exam_id)
    return load_answer_key(p) if os.path.exists(p) else None


# ---------------- 사진 → P1·P2·P3 → 저장 ----------------
def decode_image(data: bytes) -> np.ndarray:
    """업로드된 파일 바이트 → OpenCV 이미지(BGR). 이미지가 아니면 ValueError.

    매개변수:
        data: 파일 내용 바이트 (jpg/png 등)
    반환:
        이미지 배열 (numpy, 모양 (높이, 너비, 3), 색 순서 BGR)
    예외:
        ValueError: OpenCV가 이미지로 읽지 못함 (이미지 파일이 아니거나 깨짐)
    """
    # 바이트 → 1차원 uint8 배열 → 컬러 이미지로 해석. 실패하면 OpenCV는 None을 돌려줌
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("이미지 파일을 읽을 수 없습니다 (jpg/png만 가능)")
    return img


def process_photo(exam_id: str, photo_bgr: np.ndarray, source: str = "",
                  cfg: ReadConfig | None = None) -> dict[str, Any]:
    """사진 1장을 파이프라인 P1정렬 → P2시험 확인 → P3판독에 통과시키고 결과를 저장.
    - 정렬 실패      → status "align_failed"로 저장 (엑셀에 '재촬영 필요'로 표시)
    - 다른 시험 답안지 → status "wrong_exam", 저장하지 않고 돌려주기만 함
    - 성공           → status "read", 판독 결과(read)와 정렬 이미지 저장

    실패해도 예외를 던지지 않고 status로 알려 줌 (flow.run_photo 방식) → 여러 장 처리 중 멈추지 않음.

    매개변수:
        exam_id: 시험 ID (예: "EX-20261013-8C7353")
        photo_bgr: 답안지 사진 (decode_image 결과, BGR 이미지 배열)
        source: 원래 파일 이름 등 사진 출처 (예: "photo1.jpg"). 엑셀·목록에 표시
        cfg: 판독 기준값 (ReadConfig). None이면 기본값 (마킹 0.60 / 애매 0.25)
    반환:
        답안지 기록 dict (모듈 설명의 sheets/<sheet_id>.json 내용과 같음). 예:
        {"sheet_id": "S-1A2B3C4D", "exam_id": "EX-...", "source": "photo1.jpg",
         "processed_at": "2026-10-13T15:00:00+09:00", "status": "read", "error": None,
         "notes": [], "align": {...}, "read": {...},
         "review": {"corrections": {}, "student_id": None}}
    예외:
        ExamNotFoundError: 시험이 없음
        RuntimeError: 양식 파일이 없음
    """
    exam, layout = _exam_and_layout(exam_id)
    result = flow.run_photo(photo_bgr, exam, layout, key=None, cfg=cfg, source=source)   # P1·P2·P3

    # 답안지 ID: "S-" + 무작위 4바이트를 16진수 대문자 8자리로 (예: S-1A2B3C4D)
    sheet_id = f"S-{secrets.token_hex(4).upper()}"
    rec: dict[str, Any] = {
        "sheet_id": sheet_id, "exam_id": exam_id, "source": source,
        "processed_at": datetime.now(KST).isoformat(timespec="seconds"),
        "status": result.status, "error": result.error, "notes": result.notes,
        "align": result.align_summary(),
        "read": result.read,                                    # P4의 입력이 될 값
        "review": {"corrections": {}, "student_id": None},      # 검수에서 채울 칸
    }
    # 다른 시험 답안지는 이 시험 폴더에 남기지 않음
    if result.status == flow.WRONG_EXAM:
        return rec
    # 정렬 이미지(P1 출력)가 있으면 검수 화면용으로 JPEG(품질 85) 저장
    if result.align is not None:
        cv2.imwrite(os.path.join(_sheets_dir(exam_id), f"{sheet_id}_aligned.jpg"), result.align.warped,
                    [cv2.IMWRITE_JPEG_QUALITY, 85])
    return _save_sheet(rec)


def _save_sheet(rec: dict[str, Any]) -> dict[str, Any]:
    """답안지 기록을 sheets/<sheet_id>.json 으로 저장 (같은 ID면 덮어씀).

    매개변수:
        rec: 답안지 기록 dict ("exam_id", "sheet_id" 키 필수)
    반환:
        저장한 rec 그대로 (호출한 쪽에서 바로 return 하기 편하게)
    예외:
        ExamNotFoundError: 시험이 없음
    """
    with open(os.path.join(_sheets_dir(rec["exam_id"]), f"{rec['sheet_id']}.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    return rec


def list_sheets(exam_id: str) -> list[dict[str, Any]]:
    """저장된 답안지 기록 전체 (처리 시각 순).

    sheets 폴더의 .json 파일을 모두 읽음 (_aligned.jpg 는 건너뜀).
    파일 이름은 무작위 ID라 순서 의미가 없으므로, 마지막에 processed_at(처리 시각)으로 다시 정렬함.

    매개변수:
        exam_id: 시험 ID (예: "EX-20261013-8C7353")
    반환:
        답안지 기록 dict 의 목록 (process_photo 반환값과 같은 형식). 없으면 []
    예외:
        ExamNotFoundError: 시험이 없음
    """
    d = _sheets_dir(exam_id)
    out = []
    for name in sorted(os.listdir(d)):
        if name.endswith(".json"):
            with open(os.path.join(d, name), encoding="utf-8") as f:
                out.append(json.load(f))
    return sorted(out, key=lambda r: r["processed_at"])


def get_sheet(exam_id: str, sheet_id: str) -> dict[str, Any] | None:
    """답안지 한 장의 기록 읽기.

    sheet_id 형식을 먼저 검사해서 '../' 같은 경로 조작을 막음.

    매개변수:
        exam_id: 시험 ID (예: "EX-20261013-8C7353")
        sheet_id: 답안지 ID (예: "S-1A2B3C4D")
    반환:
        답안지 기록 dict. ID 형식이 틀리거나 파일이 없으면 None
    예외:
        ExamNotFoundError: 시험이 없음
    """
    if not SHEET_ID_RE.match(sheet_id):
        return None
    p = os.path.join(_sheets_dir(exam_id), f"{sheet_id}.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def aligned_image_path(exam_id: str, sheet_id: str) -> str | None:
    """답안지 한 장의 정렬 이미지(P1 출력, sheets/<sheet_id>_aligned.jpg) 경로. 검수 화면에 보여 줄 사진.

    매개변수:
        exam_id: 시험 ID (예: "EX-20261013-8C7353")
        sheet_id: 답안지 ID (예: "S-1A2B3C4D")
    반환:
        이미지 파일 경로. ID 형식이 틀리거나(경로 조작 방지) 정렬에 실패해 이미지가 없으면 None
    예외:
        ExamNotFoundError: 시험이 없음
    """
    if not SHEET_ID_RE.match(sheet_id):
        return None
    p = os.path.join(_sheets_dir(exam_id), f"{sheet_id}_aligned.jpg")
    return p if os.path.exists(p) else None


def review_sheet(exam_id: str, sheet_id: str, corrections: dict[Any, Any] | None = None,
                 student_id: str | None = None) -> dict[str, Any]:
    """검수에서 선생님이 확인·수정한 답 기록. 판독 원본(read)은 그대로 두고 review 칸에만 씀
    → 나중에 '자동 판독 vs 사람 수정' 비교(검수 부담 실험, RQ4)에 그대로 쓸 수 있음.

    여러 번 부르면 문항별 수정값이 누적됨 (같은 문항은 마지막 값으로 덮어씀).

    매개변수:
        exam_id: 시험 ID (예: "EX-20261013-8C7353")
        sheet_id: 답안지 ID (예: "S-1A2B3C4D")
        corrections: 문항별 확정 답 (예: {"7": 2, "9": [], "12": "1,4"}). None이면 답은 안 고침
        student_id: 확정 학번 (숫자만, 예: "2026123456"). None이면 학번은 안 고침
    반환:
        저장된 답안지 기록 dict (review 칸에 "updated_at" 수정 시각이 추가됨)
    예외:
        LookupError: 답안지가 없거나 판독 성공(status "read") 상태가 아님
        ValueError: 학번에 숫자 외 문자가 있음
        ExamNotFoundError: 시험이 없음
    """
    rec = get_sheet(exam_id, sheet_id)
    if not rec or rec["status"] != flow.READ:
        raise LookupError("판독된 답안지를 찾을 수 없습니다")
    # 문항 번호는 "07" 같은 표기도 int → str 로 바꿔 "7" 로 통일 (JSON 키는 문자열만 가능)
    for q, v in (corrections or {}).items():
        rec["review"]["corrections"][str(int(q))] = parse_choices(v)   # "2" / [1,4] / "" → [2] / [1,4] / []
    if student_id is not None:
        if not re.fullmatch(r"\d+", student_id):
            raise ValueError("학번은 숫자만 입력")
        rec["review"]["student_id"] = student_id
    rec["review"]["updated_at"] = datetime.now(KST).isoformat(timespec="seconds")
    return _save_sheet(rec)


# ---------------- P4 채점 → P5 엑셀 ----------------
def grade_sheets(exam_id: str, key: AnswerKey | None = None) -> list[dict[str, Any]]:
    """저장된 판독 결과(P3) + 최신 정답표 + 검수 수정값 → 답안지별 채점 결과(P4) 목록.

    판독 성공 장   : 검수 수정값을 반영해서 채점 → "확정" 또는 "검수 대기"
    정렬 실패 장   : 점수 없이 "재촬영 필요" 행 (retake_record, 채점 결과와 같은 키라 한 목록에 섞어 넣음)

    매개변수:
        exam_id: 시험 ID (예: "EX-20261013-8C7353")
        key: 채점에 쓸 정답표. None(생략)이면 저장된 정답표를 읽어서 씀
    반환:
        답안지별 채점 결과 dict 의 목록. 예:
        [{"sheet_id": "S-1A2B3C4D", "source": "photo1.jpg", "status": "확정",
          "student_id": "2026123456", "score": 17.0, "confirmed_score": 17.0,
          "max_possible": 17.0, "items": [...], "read": {...}, ...}, ...]
        (판독 성공 장에만 "read" 가 들어감 → 엑셀 '검수 대기' 시트가 채움 비율을 씀)
    예외:
        ValueError: 정답표가 없음
        ExamNotFoundError: 시험이 없음
    """
    key = key or get_answer_key(exam_id)
    if key is None:
        raise ValueError("정답표가 없습니다. 먼저 정답표를 입력하세요")
    out = []
    for rec in list_sheets(exam_id):
        if rec["status"] != flow.READ:
            # 정렬 실패 → '재촬영 필요' 행 (error에 실패 사유)
            g = retake_record(rec["source"], rec.get("error") or "")
        else:
            # 저장된 검수 수정값의 문항 번호("7")를 채점 함수가 쓰는 정수(7)로 바꿈
            corr = {int(q): v for q, v in rec["review"]["corrections"].items()}
            g = flow.step_grade(rec["read"], key, corr, rec["review"].get("student_id"))   # P4
            g["read"] = rec["read"]
            g["source"] = rec["source"]
        g["sheet_id"] = rec["sheet_id"]
        out.append(g)
    return out


def export_results(exam_id: str, out_path: str | None = None) -> str:
    """P4 결과 목록 → P5 엑셀 파일. 경로 생략 시 시험 폴더의 results.xlsx.

    매개변수:
        exam_id: 시험 ID (예: "EX-20261013-8C7353")
        out_path: 엑셀을 저장할 경로 (예: "결과.xlsx"). None이면 시험 폴더의 results.xlsx (덮어씀)
    반환:
        만든 엑셀 파일 경로 문자열
    예외:
        ValueError: 정답표가 없음
        ExamNotFoundError: 시험이 없음
    """
    key = get_answer_key(exam_id)
    if key is None:
        raise ValueError("정답표가 없습니다. 먼저 정답표를 입력하세요")
    records = grade_sheets(exam_id, key)
    path = out_path or os.path.join(_exam_dir(exam_id), "results.xlsx")
    return flow.step_export(records, key, service.get_exam(exam_id), path)                 # P5
