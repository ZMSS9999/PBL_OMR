# pyright: basic
"""전체 파이프라인 연결부 — 각 모듈의 출력이 다음 모듈의 입력이 되도록 한곳에서 이어 줌.

[이 파일의 역할]
  - 정렬(align.py), 판독(read.py), 채점(grading/score.py), 엑셀(grading/excel.py)은
    서로를 모르고 각자 "받는 것 → 내놓는 것"만 정해 둠
  - 이 파일만 단계 순서를 알고, 앞 단계 출력을 다음 단계 입력으로 넘겨 줌
  - P2 시험 확인(다른 시험 답안지가 섞였는지 검사)은 이 파일 안에서 직접 처리함

[흐름 한눈에 보기]  (P = Pipeline 단계. 코드 주석 전체에서 같은 번호 사용)

  (준비) 답안지 생성: sheetgen.compute_layout(SheetSpec) → layout(dict)  ─┐  (시험 만들 때 1번)
                                                                         │  layout.json으로 저장
  사진 1장 ───────────────────────────────────────────────────────────────┼──────────────
  P1 step_align(photo)            → AlignResult  (정렬 이미지 warped, QR 정보, 방향)
  P2 step_check_exam(AlignResult, exam, layout) → notes (다른 시험/방향이면 WrongSheetError)
  P3 step_read(AlignResult.warped, layout)       → read(dict)   (문항별 답, 학번)
  P4 step_grade(read, AnswerKey)                 → grade(dict)  (문항별 정오, 점수, 확정 여부)
  ─────────────────────────────────────────────────────────────────────────────────────
  여러 장 모아서
  P5 step_export([grade, ...], AnswerKey, exam)  → 결과 엑셀 경로

[단계 사이에서 주고받는 데이터 = '계약']
  - layout   : 양식 좌표 JSON. 키 이름은 sheetgen/layout.py가 정함 (mcq.questions[].choices[].center_mm 등)
  - AlignResult: pipeline/align.py의 dataclass. 다음 단계는 .warped(200dpi 정면 이미지)만 씀
  - read     : pipeline/read.py의 read_sheet() 반환값. 다음 단계는 read["mcq"][i]["answer"],
               read["student_id"]["text"/"raw"]만 씀
  - grade    : grading/score.py의 grade_sheet() 반환값. 엑셀은 이 dict만 보고 그림
  - exam     : 시험 정보 dict. 이 파일에서는 exam["exam_id"]만 씀 (P2에서 QR의 시험 ID와 비교).
               P5 엑셀은 제목줄에 exam_name·subject·exam_id를 씀
  → 단계 하나를 바꿔도(예: P3을 규칙+CNN으로 교체) 위 키만 지키면 앞뒤 단계는 안 고쳐도 됨.

[이 파일의 역할 구분]
  - 여기(flow.py) : 저장 없이 메모리 안에서만 단계를 이어 줌 → 실험·테스트·노트북에서 바로 사용
  - app/grading_service.py : 같은 함수를 부르고 결과를 storage/에 파일로 저장 → 웹·명령행에서 사용

[다른 파일과의 관계]
  - 이 파일을 부르는 쪽 : app/grading_service.py, tools/run_pipeline.py, tools/synth_eval.py
  - 이 파일이 부르는 쪽 : pipeline/align.py(align), pipeline/read.py(read_sheet),
                          grading/score.py(grade_sheet, retake_record), grading/excel.py(write_results_xlsx),
                          grading/answer_key.py(AnswerKey: 정답표 자료형)

[주요 함수/클래스]
  - WrongSheetError        : 다른 시험·다른 방향 답안지일 때 P2에서 나는 예외
  - READ / ALIGN_FAILED / WRONG_EXAM : SheetResult.status 값
  - SheetResult            : 사진 한 장의 단계별 결과 묶음 (+ align_summary(): 저장용 요약)
  - step_align()           : P1 정렬
  - step_check_exam()      : P2 시험 확인
  - step_read()            : P3 판독
  - step_grade()           : P4 채점
  - step_export()          : P5 엑셀 저장
  - run_photo()            : 사진 1장에 P1→P2→P3(→P4) 적용
  - run_batch()            : 사진 여러 장에 run_photo 반복 후 P5 엑셀까지

[사용 예]
  from pipeline import flow
  results, xlsx = flow.run_batch([("a.jpg", img_a), ("b.jpg", img_b)], exam, layout, key, "결과.xlsx")
  for r in results:
      print(r.source, r.status, r.error)     # 예: a.jpg read None / b.jpg align_failed 마커 2개만 검출됨 ...
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from grading.answer_key import AnswerKey
from grading.excel import write_results_xlsx
from grading.score import grade_sheet, retake_record
from pipeline.align import AlignError, AlignResult, align
from pipeline.read import ReadConfig, read_sheet


class WrongSheetError(ValueError):
    """다른 시험(또는 다른 방향 양식)의 답안지일 때 P2에서 발생.

    step_check_exam()이 던지고, run_photo()가 잡아서 SheetResult.status = "wrong_exam"으로 기록함.
    메시지는 그대로 SheetResult.error에 들어감.
    """


# 상태 값 (SheetResult.status)
READ = "read"                    # P1~P3 통과, 판독 결과 있음
ALIGN_FAILED = "align_failed"    # P1 실패 → 재촬영 필요
WRONG_EXAM = "wrong_exam"        # P2 실패 → 이 시험 결과에 넣지 않음


@dataclass
class SheetResult:
    """사진 한 장이 파이프라인을 지나며 쌓이는 결과 묶음.
    단계마다 자기 칸만 채움 → 어디서 멈췄는지 status/error로 바로 보임.

    예: P1에서 실패하면 status="align_failed", error=실패 사유, align/read/grade는 모두 None
    """
    source: str                                  # 파일명 등 사진 출처
    status: str = READ                           # READ / ALIGN_FAILED / WRONG_EXAM 중 하나
    error: str | None = None                     # 실패 사유 (학생 안내 문구로도 사용)
    notes: list[str] = field(default_factory=list)   # 경고 메모 (P1 경고 + P2 경고. 예: "QR을 읽지 못해 ...")
    align: AlignResult | None = None             # P1 결과
    read: dict[str, Any] | None = None           # P3 결과
    grade: dict[str, Any] | None = None          # P4 결과 (정답표가 있을 때만)

    def align_summary(self) -> dict[str, Any] | None:
        """저장용 요약 (이미지·행렬은 빼고 숫자만).

        app/grading_service.py가 결과를 JSON으로 저장할 때 씀
        (warped 이미지·H 행렬은 JSON에 넣을 수 없고 크기도 커서 뺌).

        매개변수:
            없음 (self.align 사용)
        반환:
            P1 결과가 없으면 None, 있으면 dict
            예: {"orientation": "portrait", "markers": [0, 1, 2, 3],
                 "reproj_err_px": 0.41, "blur_var": 523.7, "qr_ok": True}
              orientation   : 답안지 방향
              markers       : 정렬에 쓴 마커 ID 목록
              reproj_err_px : 정렬 오차 (px, 소수 2자리)
              blur_var      : 흐림 지표 (소수 1자리, 낮을수록 흐림)
              qr_ok         : QR을 읽고 해석까지 성공했는지
              marker_set    : 정렬에 쓴 마커 세트 ("team" 팀 양식 / "print" 실제 인쇄본)
        """
        a = self.align
        if a is None:
            return None
        return {"orientation": a.orientation, "markers": a.found_ids,
                "reproj_err_px": round(a.reproj_err_px, 2), "blur_var": round(a.blur_var, 1),
                "qr_ok": a.qr_info is not None, "marker_set": a.marker_set}


# ---------------- 단계 함수 (각각 단독으로도 호출 가능) ----------------
def step_align(photo_bgr: np.ndarray) -> AlignResult:
    """P1 사진 → 마커 검출 → 정면 이미지(warped) + QR 정보. 실패하면 AlignError.

    pipeline/align.py의 align()을 기본 설정(200dpi, 마커 최소 3개)으로 부름.

    매개변수:
        photo_bgr: 원본 사진 (OpenCV BGR 배열)
    반환:
        AlignResult (warped, qr_info, orientation 등)
    예외:
        AlignError: 마커 부족 등으로 정렬 불가 → 재촬영 필요
    """
    return align(photo_bgr)


def step_check_exam(res: AlignResult, exam: dict[str, Any], layout: dict[str, Any]) -> list[str]:
    """P2 이 사진이 지금 채점하는 시험의 답안지가 맞는지 확인.
    - QR의 시험 ID가 다르면 → WrongSheetError (다른 반/다른 시험 답안지가 섞인 경우)
    - QR을 못 읽으면 → 경고만 남기고 선택한 시험 양식으로 계속 진행
    - 마커로 알아낸 방향(세로/가로)이 양식과 다르면 → WrongSheetError

    매개변수:
        res: P1 결과 (qr_info, orientation, notes 사용)
        exam: 지금 채점 중인 시험 정보 dict (exam["exam_id"] 사용. 예: {"exam_id": "EX-...", ...})
        layout: 이 시험의 양식 좌표 dict (layout["orientation"] 사용: "portrait" / "landscape")
    반환:
        메모 목록 (P1의 notes를 복사한 뒤 P2 경고를 덧붙인 것)
        예: ["마커 1개 누락 — 나머지로 정렬함", "QR을 읽지 못해 선택한 시험의 양식으로 판독함"]
    예외:
        WrongSheetError: QR의 시험 ID가 다르거나, 답안지 방향이 양식과 다를 때
    """
    notes = list(res.notes)   # 원본(res.notes)을 바꾸지 않게 복사
    if res.qr_info:
        if res.qr_info["exam_id"] != exam["exam_id"]:
            raise WrongSheetError(f"다른 시험의 답안지입니다 (QR 시험 ID {res.qr_info['exam_id']})")
    else:
        notes.append("QR을 읽지 못해 선택한 시험의 양식으로 판독함")
    if res.orientation != layout["orientation"]:
        raise WrongSheetError("답안지 방향(세로/가로)이 시험 양식과 다릅니다")
    return notes


def step_read(warped: np.ndarray, layout: dict[str, Any], cfg: ReadConfig | None = None) -> dict[str, Any]:
    """P3 정렬 이미지 + 양식 좌표 → 문항별 답·학번 (규칙 기반). 3단계에서 CNN 판독기로 교체할 자리.

    매개변수:
        warped: P1 정렬 이미지 (AlignResult.warped)
        layout: 양식 좌표 dict
        cfg: 판독 기준값 (None이면 read.py의 기본값)
    반환:
        read dict (pipeline/read.py의 read_sheet() 설명 참고)
        예: read["mcq"][0] = {"q": 1, "answer": [3], "status": "ok", ...}
    """
    return read_sheet(warped, layout, cfg)


def step_grade(read: dict[str, Any], key: AnswerKey,
               corrections: dict[int, list[int]] | None = None,
               student_id: str | None = None) -> dict[str, Any]:
    """P4 판독 결과 + 정답표 → 점수. corrections/student_id는 검수에서 고친 값.

    매개변수:
        read: P3 결과 dict
        key: 정답표 (AnswerKey. 문항 수·문항별 정답·배점)
        corrections: 사람이 검수에서 고친 답 {문항 번호: 답 목록}. 예: {7: [2]}. 없으면 None
        student_id: 사람이 검수에서 고친 학번 문자열. 예: "2026103456". 없으면 None
    반환:
        grade dict (grading/score.py의 grade_sheet() 반환값)
        주요 키: student_id, student_id_raw, student_id_ok, status("확정"/"검수 대기"), score,
                 confirmed_score, max_possible, total_points, pending_questions, counts, items
    예외:
        ValueError: 답안지 문항 수와 정답표 문항 수가 다를 때 (grade_sheet에서 발생)
    """
    return grade_sheet(read, key, corrections, student_id)


def step_export(grades: list[dict[str, Any]], key: AnswerKey, exam: dict[str, Any] | None,
                out_path: str) -> str:
    """P5 여러 장의 채점 결과 → 엑셀 파일.

    매개변수:
        grades: grade dict 목록 (P4 결과, retake_record()로 만든 '재촬영 필요' 기록도 섞어 넣을 수 있음)
        key: 정답표 (AnswerKey)
        exam: 시험 정보 dict (엑셀 제목줄 표시용, None이어도 됨)
        out_path: 저장할 엑셀 파일 경로 (예: "결과.xlsx")
    반환:
        저장된 엑셀 파일 경로 (grading/excel.py의 write_results_xlsx() 반환값)
    """
    return write_results_xlsx(out_path, grades, key, exam)


# ---------------- 단계 잇기 ----------------
def run_photo(photo_bgr: np.ndarray, exam: dict[str, Any], layout: dict[str, Any],
              key: AnswerKey | None = None, cfg: ReadConfig | None = None,
              source: str = "") -> SheetResult:
    """사진 1장에 P1→P2→P3(→P4)를 차례로 적용. 앞 단계 출력이 그대로 다음 단계 입력이 됨.
    실패해도 예외를 밖으로 던지지 않고 SheetResult.status에 기록 → 여러 장 처리 중 한 장 때문에 멈추지 않음.

    단, 밖으로 안 던지는 것은 AlignError(P1)와 WrongSheetError(P2) 두 가지뿐이고,
    P3·P4에서 나는 다른 예외(예: 문항 수가 정답표와 달라 나는 ValueError)는 그대로 밖으로 나감.

    매개변수:
        photo_bgr: 원본 사진 (OpenCV BGR 배열)
        exam: 시험 정보 dict (P2에서 exam["exam_id"] 사용)
        layout: 양식 좌표 dict
        key: 정답표. None이면 P4 채점을 건너뛰고 판독(P3)까지만 함
        cfg: 판독 기준값 (None이면 기본값)
        source: 사진 출처 이름 (예: 파일명 "student01.jpg"). 결과와 엑셀에 표시됨
    반환:
        SheetResult. status가 "read"면 align·read(·grade)가 채워져 있음
        P4를 했으면 grade dict에 "source"(사진 출처)와 "read"(P3 결과 전체)를 덧붙여 둠
    """
    r = SheetResult(source=source)
    try:
        r.align = step_align(photo_bgr)                         # P1
    except AlignError as e:
        r.status, r.error = ALIGN_FAILED, str(e)
        return r
    try:
        r.notes = step_check_exam(r.align, exam, layout)        # P2 (P1의 출력 사용)
    except WrongSheetError as e:
        r.status, r.error = WRONG_EXAM, str(e)
        return r
    r.read = step_read(r.align.warped, layout, cfg)             # P3 (P1의 warped 사용)
    if key is not None:
        r.grade = step_grade(r.read, key)                       # P4 (P3의 출력 사용)
        r.grade["source"] = source                              # 엑셀에서 어느 사진의 결과인지 표시용
        r.grade["read"] = r.read                                # 엑셀 '검수 대기' 시트가 채움 비율을 씀
    return r


def run_batch(photos: Iterable[tuple[str, np.ndarray]], exam: dict[str, Any], layout: dict[str, Any],
              key: AnswerKey, out_xlsx: str | None = None,
              cfg: ReadConfig | None = None) -> tuple[list[SheetResult], str | None]:
    """사진 여러 장 → run_photo 반복 → P5 엑셀. (이름, 이미지) 묶음을 받음.
    다른 시험 답안지(WRONG_EXAM)는 결과에서 빼고, 정렬 실패는 '재촬영 필요' 행으로 엑셀에 남김.

    매개변수:
        photos: (이름, 사진) 튜플들. 예: [("a.jpg", img_a), ("b.jpg", img_b)]
        exam: 시험 정보 dict
        layout: 양식 좌표 dict (모든 사진에 같은 양식을 적용)
        key: 정답표 (AnswerKey)
        out_xlsx: 엑셀 저장 경로. None이면 엑셀을 만들지 않음
        cfg: 판독 기준값 (None이면 기본값)
    반환:
        (results, path) 튜플
          results : 사진 순서대로 SheetResult 목록 (다른 시험 답안지도 포함됨)
          path    : 저장한 엑셀 경로, out_xlsx가 None이면 None
    예외:
        run_photo()에서 밖으로 나오는 예외(P3·P4 오류)는 그대로 전달됨
    """
    results = [run_photo(img, exam, layout, key, cfg, name) for name, img in photos]
    # 엑셀에 넣을 행 모으기: 판독·채점 성공 → grade 그대로 / 정렬 실패 → '재촬영 필요' 기록 / 다른 시험 → 제외
    grades: list[dict[str, Any]] = []
    for r in results:
        if r.status == READ and r.grade is not None:
            grades.append(r.grade)
        elif r.status == ALIGN_FAILED:
            grades.append(retake_record(r.source, r.error or ""))
    path = step_export(grades, key, exam, out_xlsx) if out_xlsx else None   # P5 (P4의 출력 목록 사용)
    return results, path
