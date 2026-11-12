# pyright: basic
"""파이프라인 P4 채점: 판독 결과(read) + 정답표(AnswerKey) → 점수 (로드맵 2-4-2).

[이 파일의 역할]
  답안지 한 장의 판독 결과를 정답표와 비교해서 문항별 정오와 점수를 계산함.
  애매하게 읽힌 문항은 점수에 넣지 않고 '검수 대기'로 따로 표시해서,
  확정된 점수와 아직 사람이 확인해야 하는 부분을 구분함.

[입력 → 출력]
  read(P3 출력) + AnswerKey ──grade_sheet()──► grade(dict) ──► P5 엑셀(excel.py)
  read에서 쓰는 값: read["mcq"][i]["answer"], ["status"], read["student_id"]["text"/"raw"]
    - read["mcq"][i]["q"]      : 문항 번호 (1부터)
    - read["mcq"][i]["answer"] : [3] 1개 표시 / [2, 5] 복수 표시 / [] 무응답 / None 판독 보류
    - read["mcq"][i]["status"] : P3 판독 상태 "ok" / "multi" / "blank" / "ambiguous" (grade의 read_status로 옮김)
    - read["student_id"]["text"]: 읽은 학번 문자열 (예: "20261234"), 한 자리라도 못 읽었으면 None
    - read["student_id"]["raw"] : 못 읽은 자리를 "?"로 채운 판독 문자열 (예: "2026?234", 엑셀 비고·검수용)

[문항 결과]
    correct  정답          : 응답 1개이고 정답 목록에 있음
    wrong    오답          : 응답 1개이고 정답 목록에 없음
    multi    복수 응답(오답): 2개 이상 표시 → 0점, 검수 화면에서 확인할 수 있게 표시
    blank    무응답        : 0점
    pending  검수 대기     : 판독 보류(애매한 칸) → 점수에 넣지 않음

[답안지 상태]
    확정       : 검수 대기 문항이 없고 학번도 정상으로 읽힘
    검수 대기  : 검수 대기 문항이 있거나 학번이 불완전 → confirmed_score는 '현재까지 확정된 점수'
    재촬영 필요: 정렬 실패 등으로 판독 자체를 못 함 (P1 정렬 단계 실패, retake_record()로 기록)

[검수 반영]
  - 선생님이 고친 답: corrections={문항: [답]} → 판독 결과 대신 사용
  - 고친 학번: student_id_correction
  - 고친 문항은 source="review"로 기록 (엑셀에서 굵은 밑줄 표시)

[다른 파일과의 관계]
  - 이 파일을 부르는 쪽: pipeline/flow.py (step_grade, 판독 실패 시 retake_record),
                         app/grading_service.py (저장된 판독 결과 + 검수 수정값으로 다시 채점)
  - 이 파일이 부르는 쪽: answer_key.py의 AnswerKey (정답·배점 조회)
  - 결과(grade dict)를 받는 쪽: excel.py의 write_results_xlsx (P5)

[주요 상수·함수]
  - STATUS_CONFIRMED / STATUS_PENDING / STATUS_RETAKE : 답안지 상태 문자열
  - grade_question() : 문항 하나 판정 (correct / wrong / multi / blank / pending)
  - grade_sheet()    : 답안지 한 장 채점 → grade dict
  - retake_record()  : 판독 못 한 답안지용 grade dict (같은 키, 빈 값)

[사용 예]
    grade = grade_sheet(read, key)                                   # 자동 판독 결과 그대로
    grade = grade_sheet(read, key, corrections={7: [2]},            # 7번을 선생님이 2로 고침
                        student_id_correction="20261234")
    grade["status"]          # → "확정" 또는 "검수 대기"
    grade["confirmed_score"] # → 지금까지 확정된 점수 (예: 17.0)
"""
from __future__ import annotations

from typing import Any

from .answer_key import AnswerKey

# 답안지 상태 문자열 (엑셀·웹 화면에 그대로 표시되는 값)
STATUS_CONFIRMED = "확정"           # 점수 확정: 검수 대기 문항 없음 + 학번 정상
STATUS_PENDING = "검수 대기"        # 사람이 확인해야 할 문항이나 학번이 남아 있음
STATUS_RETAKE = "재촬영 필요"       # 정렬 실패 등으로 판독 자체를 못 함


def grade_question(answer: list[int] | None, key_answers: list[int]) -> str:
    """문항 하나: 읽은 답 + 정답 목록 → correct / wrong / multi / blank / pending.

    판정 순서가 중요함: 판독 보류(None) → 무응답([]) → 복수 표시(2개 이상) → 정오 비교.
    복수 정답 인정 문항(key_answers가 여러 개)은 그중 하나만 표시하면 정답.

    매개변수:
        answer: 학생 답 (예: [3] 1개 / [2, 5] 복수 / [] 무응답 / None 판독 보류)
        key_answers: 정답으로 인정할 선지 목록 (예: [3] 또는 [1, 4])
    반환:
        결과 문자열 하나
        예: grade_question([3], [3]) → "correct", grade_question([2], [1, 4]) → "wrong",
            grade_question([1, 4], [1, 4]) → "multi", grade_question([], [3]) → "blank",
            grade_question(None, [3]) → "pending"
    """
    if answer is None:
        return "pending"
    if not answer:
        return "blank"
    if len(answer) > 1:
        return "multi"
    return "correct" if answer[0] in key_answers else "wrong"


def grade_sheet(read: dict[str, Any], key: AnswerKey,
                corrections: dict[int, list[int]] | None = None,
                student_id_correction: str | None = None) -> dict[str, Any]:
    """read_sheet() 결과 한 장 채점.
    문항 수가 정답표와 다르면 ValueError (다른 양식 답안지에 정답표를 잘못 적용한 경우).

    동작:
        1. 문항마다 답을 정함: 선생님 수정값(corrections)이 있으면 그것, 없으면 판독 결과
        2. grade_question()으로 정오 판정, 정답이면 배점만큼 획득
        3. 학번: 수정값이 있으면 그것, 없으면 판독한 학번
        4. 검수 대기 문항이 없고 학번이 있으면 "확정", 아니면 "검수 대기"

    매개변수:
        read: P3 read_sheet()가 돌려준 dict (모듈 설명의 [입력 → 출력] 참고)
        key: 정답표 (AnswerKey)
        corrections: 검수에서 고친 답 {문항 번호: 선지 목록} (예: {7: [2], 12: []}, 없으면 None)
        student_id_correction: 검수에서 고친 학번 (예: "20261234", 없으면 None)
    반환:
        grade dict (P5 엑셀의 입력). 키 설명은 아래 return 부분 주석 참고
    예외:
        ValueError: 답안지 문항 수와 정답표 문항 수가 다를 때
    """
    corrections = corrections or {}
    by_q = {q["q"]: q for q in read["mcq"]}     # 문항 번호 → 판독 결과 한 문항 (번호로 바로 찾기 위함)
    if len(by_q) != key.num_questions:
        raise ValueError(f"답안지 문항 수({len(by_q)})와 정답표 문항 수({key.num_questions})가 다릅니다")

    # 문항별 채점 결과 목록 (1번부터 순서대로)
    items: list[dict[str, Any]] = []
    for qn in range(1, key.num_questions + 1):
        rq = by_q[qn]
        # 검수에서 고친 답이 있으면 그 답을 쓰고 출처를 "review"로, 없으면 판독 결과("auto")
        if qn in corrections:
            answer, source = sorted(corrections[qn]), "review"
        else:
            answer, source = rq["answer"], "auto"
        result = grade_question(answer, key.answers[qn])
        pts = key.point(qn)
        # 문항 하나의 채점 기록 (grade["items"]의 원소)
        items.append({
            "q": qn,                                          # 문항 번호
            "answer": answer,                                 # 채점에 쓴 답 ([3] / [2, 5] / [] / None)
            "key": key.answers[qn],                           # 정답 목록
            "result": result,                                 # correct / wrong / multi / blank / pending
            "points": pts,                                    # 이 문항 배점
            "earned": pts if result == "correct" else 0.0,    # 얻은 점수 (정답일 때만 배점만큼)
            "read_status": rq["status"],                      # P3 판독 상태 (수정했어도 원래 판독 상태 유지)
            "source": source,                                 # "auto" 자동 판독 / "review" 검수 수정
        })

    sid = read["student_id"]
    student_id = student_id_correction or sid["text"]   # 최종 학번 (수정값 우선, 못 읽었으면 None)
    sid_ok = student_id is not None                     # 학번 확정 여부
    pending = [it["q"] for it in items if it["result"] == "pending"]                   # 검수 대기 문항 번호 목록
    confirmed_score = sum(it["earned"] for it in items)                                # 확정된 문항에서 얻은 점수 합
    pending_points = sum(it["points"] for it in items if it["result"] == "pending")    # 검수 대기 문항 배점 합
    # 결과 종류별 문항 수 (예: {"correct": 15, "wrong": 3, "multi": 1, "blank": 0, "pending": 1})
    counts = {r: sum(it["result"] == r for it in items)
              for r in ("correct", "wrong", "multi", "blank", "pending")}
    status = STATUS_CONFIRMED if not pending and sid_ok else STATUS_PENDING
    # grade dict: P5 엑셀과 웹 화면이 이 키만 보고 동작함 (retake_record()도 같은 키)
    return {
        "student_id": student_id,                                           # 최종 학번 (없으면 None)
        "student_id_raw": sid["raw"],                                       # 판독한 원래 학번 문자열
        "student_id_ok": sid_ok,                                            # 학번 확정 여부 (True/False)
        "status": status,                                                   # "확정" / "검수 대기"
        "score": confirmed_score if status == STATUS_CONFIRMED else None,   # 확정 점수 (확정일 때만)
        "confirmed_score": confirmed_score,                                 # 지금까지 확정된 점수
        "max_possible": confirmed_score + pending_points,                   # 검수 결과에 따라 가능한 최고점
        "total_points": key.total_points,                                   # 만점
        "pending_questions": pending,                                       # 검수 대기 문항 번호 목록
        "counts": counts,                                                   # 결과 종류별 문항 수
        "items": items,                                                     # 문항별 채점 기록 목록
    }


def retake_record(source: str, error: str) -> dict[str, Any]:
    """정렬 실패 등 판독 자체를 못 한 답안지의 기록. grade_sheet() 결과와 같은 키를 가짐
    → 엑셀 쪽에서 따로 구분 처리 없이 같은 목록에 넣어 '재촬영 필요' 행으로 표시.

    점수 관련 값은 비워 두고(None, 0.0, 빈 목록) status만 "재촬영 필요"로 둠.
    grade_sheet() 결과에는 없는 "source"(파일명)와 "error"(실패 사유) 키가 추가로 들어감.

    매개변수:
        source: 사진 파일 이름 (예: "IMG_0012.jpg")
        error: 판독 실패 사유 메시지 (엑셀 '비고' 칸과 '검수 대기' 시트에 표시)
    반환:
        grade dict (status="재촬영 필요")
    """
    return {"source": source, "status": STATUS_RETAKE, "error": error, "student_id": None,
            "student_id_raw": None, "student_id_ok": False, "score": None, "confirmed_score": 0.0,
            "max_possible": None, "total_points": None, "pending_questions": [], "counts": {}, "items": []}
