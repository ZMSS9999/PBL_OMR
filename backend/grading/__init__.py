# pyright: basic
"""grading 패키지 — 파이프라인 P4 채점과 P5 엑셀 출력 담당.

[역할]
  P3 판독 결과(read)와 선생님이 입력한 정답표를 받아 점수를 매기고(P4), 결과를 엑셀로 만듦(P5).
  사진 처리(P1~P3)는 pipeline/ 패키지 담당이고, 이 패키지는 이미 읽힌 답만 다룸.

[파일 구성]
  - answer_key.py : 정답표·배점 (AnswerKey), JSON/엑셀 정답표 읽기·저장, 빈 정답표 양식 만들기
  - score.py      : P4 채점. read + AnswerKey → grade dict (확정 / 검수 대기 / 재촬영 필요 구분)
  - excel.py      : P5 엑셀. grade 목록 + AnswerKey → 결과 .xlsx (시트 5개)

[입력 → 출력]
  read(P3) + AnswerKey ──grade_sheet()──► grade ──(여러 장 모음)──► write_results_xlsx() ──► 엑셀 파일

[이 패키지를 부르는 쪽]
  pipeline/flow.py (step_grade, step_export), app/grading_service.py, tools/run_pipeline.py

[여기서 바로 꺼내 쓸 수 있는 이름] (from grading import ...)
  - AnswerKey, AnswerKeyError                : 정답표 자료형과 정답표 오류
  - load_answer_key, save_answer_key         : 정답표 파일 읽기 / JSON 저장
  - answer_key_template                      : 빈 정답표 엑셀 만들기
  - parse_choices                            : 답 표기 → 선지 번호 목록
  - grade_sheet, retake_record               : 답안지 한 장 채점 / 판독 실패 답안지 기록
  - STATUS_CONFIRMED, STATUS_PENDING, STATUS_RETAKE : 답안지 상태 문자열 ("확정" / "검수 대기" / "재촬영 필요")
  - write_results_xlsx                       : 채점 결과 엑셀 만들기

[사용 예]
    from grading import load_answer_key, grade_sheet, write_results_xlsx
    key = load_answer_key("정답표.xlsx", num_choices=5)
    grade = grade_sheet(read, key)                     # read = P3 read_sheet() 결과
    write_results_xlsx("결과.xlsx", [grade], key)
"""
from .answer_key import (AnswerKey, AnswerKeyError, answer_key_template, load_answer_key,
                         parse_choices, save_answer_key)
from .excel import write_results_xlsx
from .score import STATUS_CONFIRMED, STATUS_PENDING, STATUS_RETAKE, grade_sheet, retake_record
