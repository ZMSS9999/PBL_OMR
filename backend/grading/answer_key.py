# pyright: basic
"""시험별 정답표와 배점 — 파이프라인 P4 채점의 입력 (로드맵 2-4-1).

[이 파일의 역할]
  - 선생님이 입력한 정답·배점을 AnswerKey 객체 하나로 정리함
  - JSON 파일 / 엑셀(.xlsx) 파일 / 웹 입력(dict) 어느 쪽으로 들어와도 같은 AnswerKey로 바꿔 줌
  - 선생님이 채워 넣을 빈 정답표 엑셀 양식도 여기서 만듦

[파이프라인에서 위치]
  P4 채점(score.grade_sheet)과 P5 엑셀(excel.write_results_xlsx)이 이 파일의 AnswerKey를 입력으로 받음
  → 이 파일은 사진·판독과 무관하고, "무엇이 정답인지"만 담당함

[용어 구분]
  - 정답표   : 시험 문제의 맞는 답 (선생님이 입력)
  - 판독 라벨: 학생이 실제로 표시한 답 (판독 정확도 평가용) → 둘은 다른 것

[저장 형식] (JSON, save_answer_key()가 쓰고 load_answer_key()가 읽음)
    {
      "exam_id": "EX-20261012-ABC123",           # 시험 ID (답안지 QR에 들어 있는 값과 같음)
      "num_questions": 20, "num_choices": 5,     # 객관식 문항 수, 문항당 선지 수
      "answers": {"1": [3], "2": [1, 4], ...},   # 여러 개면 그중 하나만 표시해도 정답 (복수 정답 인정)
      "points":  {"1": 2, ...},                   # 없으면 default_points
      "default_points": 1                         # 배점을 따로 안 적은 문항의 기본 배점
    }
    JSON은 키로 문자열만 쓸 수 있어서 문항 번호를 "1", "2" 처럼 문자열로 저장함
    (파이썬 안에서는 정수 1, 2로 바꿔서 씀)

[엑셀 입력]
  answer_key_template()으로 빈 양식 생성 → 선생님이 '문항 | 정답 | 배점' 열 채움 → load_answer_key()로 읽기
  엑셀 양식 모양:
      1행  시험 ID | EX-...
      2행  선지 수 | 5
      3행  작성법  | (안내 문구)
      4행  (빈 줄)
      5행  문항 | 정답 | 배점      ← 머리글 행
      6행~ 1    |      | 1         ← 문항별 행 (정답 칸을 선생님이 채움)
  정답 칸 표기: 숫자 3 하나, 또는 "1,4"처럼 쉼표로 구분한 복수 정답 인정 표기
  (parse_choices() 참고. 정답 칸이 문자열로 들어오는 경우의 현재 동작은 그 함수 설명의 '주의' 참고)

[다른 파일과의 관계]
  - 이 파일을 부르는 쪽:
      app/grading_service.py : 정답표 저장·불러오기, 빈 양식 만들기, 검수 수정값 해석(parse_choices)
      tools/run_pipeline.py  : 명령행에서 정답표 파일 읽기(load_answer_key)
      grading/score.py       : AnswerKey로 채점 (P4)
      grading/excel.py       : AnswerKey로 정답 행·정답표 시트 작성 (P5)
  - 이 파일이 부르는 쪽: 표준 라이브러리(json, os, re)와 openpyxl(엑셀 읽기·쓰기)만 사용

[주요 클래스·함수]
  - AnswerKeyError       : 정답표 내용·형식 오류 (선생님에게 그대로 보여 줄 메시지)
  - AnswerKey            : 정답표 한 개 (정답·배점·검사·dict 변환)
  - parse_choices()      : 여러 가지 답 표기 → 정렬된 선지 번호 목록
  - save_answer_key()    : AnswerKey → JSON 파일 저장
  - load_answer_key()    : JSON 또는 엑셀 파일 → AnswerKey
  - _load_xlsx()         : 엑셀 정답표 읽기 (load_answer_key 내부용)
  - answer_key_template(): 선생님이 채울 빈 정답표 엑셀 만들기

[흐름]
  엑셀/JSON/웹 입력 ──► AnswerKey ──► score.grade_sheet(read, key)

[사용 예]
    key = AnswerKey(num_questions=3, num_choices=5,
                    answers={1: [3], 2: [1, 4], 3: [2]}, points={3: 2})
    key.validate()                              # 문제 있으면 AnswerKeyError
    key.total_points                            # → 4.0 (1 + 1 + 2)
    save_answer_key(key, "storage/key.json")
    key2 = load_answer_key("정답표.xlsx", num_choices=5, exam_id="EX-20261012-ABC123")
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.worksheet import Worksheet


class AnswerKeyError(ValueError):
    """정답표 내용·형식 오류. 메시지는 선생님에게 그대로 보여 줄 수 있게 작성.

    ValueError를 물려받았으므로 ValueError로 잡아도 함께 잡힘.
    웹(app/main.py 쪽)에서는 이 오류를 잡아 422 응답의 메시지로 돌려줌.
    예: AnswerKeyError("정답이 비어 있는 문항: [3, 7]")
    """


@dataclass
class AnswerKey:
    """정답표 한 개. 문항 번호는 1부터, answers[q] = 정답으로 인정할 선지 목록.

    채점(P4)과 엑셀 출력(P5)이 공통으로 쓰는 정답표 자료형.
    answers의 값이 [1, 4]처럼 여러 개면 "1 또는 4를 표시하면 정답"(복수 정답 인정)이라는 뜻.

    필드:
        num_questions: 객관식 문항 수 (예: 20)
        num_choices: 문항당 선지 수 (예: 5 → 선지 번호 1~5)
        answers: {문항 번호: 정답 선지 목록} (예: {1: [3], 2: [1, 4]})
        points: {문항 번호: 배점}. 따로 정한 문항만 들어 있음 (예: {5: 2.0})
        default_points: points에 없는 문항의 배점 (기본 1.0)
        exam_id: 어느 시험의 정답표인지 (예: "EX-20261012-ABC123", 모르면 "")
    """
    num_questions: int                                      # 문항 수 (1번 ~ num_questions번)
    num_choices: int                                        # 선지 수 (선지 번호 1 ~ num_choices)
    answers: dict[int, list[int]]                           # 문항 번호 → 정답으로 인정할 선지 번호 목록
    points: dict[int, float] = field(default_factory=dict)  # 문항 번호 → 배점 (따로 정한 문항만)
    default_points: float = 1.0                             # points에 없는 문항의 기본 배점
    exam_id: str = ""                                       # 시험 ID (없으면 빈 문자열)

    def point(self, q: int) -> float:
        """q번 배점. 따로 안 적은 문항은 default_points.

        매개변수:
            q: 문항 번호 (1부터, 예: 3)
        반환:
            float 배점 (예: points={3: 2.0}이면 point(3) → 2.0, point(4) → default_points)
        """
        return self.points.get(q, self.default_points)

    @property
    def total_points(self) -> float:
        """만점 = 1번 ~ num_questions번 문항 배점의 합.

        속성처럼 key.total_points 로 씀 (괄호 없음).
        반환:
            float 만점 (예: 20문항, 기본 배점 1 → 20.0)
        """
        return sum(self.point(q) for q in range(1, self.num_questions + 1))

    def validate(self) -> None:
        """빈 정답, 범위 밖 문항·선지, 음수 배점 검사. 문제 있으면 AnswerKeyError.

        검사 순서:
            1. 1번 ~ num_questions번 중 정답이 없거나 빈 목록인 문항이 있는지
            2. answers에 문항 범위(1 ~ num_questions) 밖의 번호가 있는지
            3. 정답 선지가 선지 범위(1 ~ num_choices) 밖인지
            4. 배점이 음수인 문항이 있는지
        반환:
            없음 (문제가 없으면 그냥 끝남)
        예외:
            AnswerKeyError: 위 검사 중 하나라도 걸리면 (처음 걸린 것 하나만 알려 줌)
        """
        # 1. 정답이 비어 있는 문항 모으기 (answers에 없거나 [] 인 문항)
        missing = [q for q in range(1, self.num_questions + 1) if not self.answers.get(q)]
        if missing:
            raise AnswerKeyError(f"정답이 비어 있는 문항: {missing}")
        # 2·3. 문항 번호와 정답 선지 번호가 범위 안인지
        for q, ans in self.answers.items():
            if not 1 <= q <= self.num_questions:
                raise AnswerKeyError(f"{q}번은 문항 범위(1~{self.num_questions}) 밖입니다")
            bad = [a for a in ans if not 1 <= a <= self.num_choices]   # 범위 밖 선지 번호들
            if bad:
                raise AnswerKeyError(f"{q}번 정답 {bad}이(가) 선지 범위(1~{self.num_choices}) 밖입니다")
        # 4. 음수 배점 금지 (0점 문항은 허용)
        for q, p in self.points.items():
            if p < 0:
                raise AnswerKeyError(f"{q}번 배점이 음수입니다")

    def to_dict(self) -> dict[str, Any]:
        """JSON 저장용. JSON 키는 문자열만 되므로 문항 번호를 str로 바꿈.

        문항 순서대로 정렬해서 넣으므로 저장된 JSON 파일을 사람이 읽기 쉬움.
        반환:
            dict (모듈 설명의 [저장 형식]과 같은 모양)
            예: {"exam_id": "EX-...", "num_questions": 2, "num_choices": 5,
                 "answers": {"1": [3], "2": [1, 4]}, "points": {}, "default_points": 1.0}
        """
        return {
            "exam_id": self.exam_id,
            "num_questions": self.num_questions,
            "num_choices": self.num_choices,
            "answers": {str(q): a for q, a in sorted(self.answers.items())},   # 문항 번호 → 문자열 키
            "points": {str(q): p for q, p in sorted(self.points.items())},
            "default_points": self.default_points,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AnswerKey":
        """JSON/웹 입력 → AnswerKey. 형식이 틀리면 AnswerKeyError (웹에서 422로 돌려줌).

        to_dict()의 반대 방향. 문자열 키 "1"은 정수 1로, 답 표기는 parse_choices()로 목록으로 바꿈.
        만든 뒤 validate()까지 돌려서 내용 검사를 통과한 정답표만 돌려줌.

        매개변수:
            d: 정답표 dict. 필수 키 num_questions, num_choices, answers /
               선택 키 points, default_points(없으면 1), exam_id(없으면 "")
               예: {"num_questions": 2, "num_choices": 5, "answers": {"1": [3], "2": [1, 4]}}
        반환:
            검사를 통과한 AnswerKey
        예외:
            AnswerKeyError: 필수 키 누락, 숫자로 못 바꾸는 값, validate() 검사 실패
        """
        try:
            key = cls(
                num_questions=int(d["num_questions"]),
                num_choices=int(d["num_choices"]),
                answers={int(q): parse_choices(a) for q, a in d["answers"].items()},
                points={int(q): float(p) for q, p in (d.get("points") or {}).items()},
                default_points=float(d.get("default_points", 1)),
                exam_id=str(d.get("exam_id", "")),
            )
        except (KeyError, TypeError, ValueError) as e:      # 키 누락, 숫자 아닌 값 등
            raise AnswerKeyError(f"정답표 형식 오류: {e}") from e
        key.validate()
        return key


def parse_choices(v: Any) -> list[int]:
    """여러 표기 → 정렬된 선지 번호 목록. 예: 3 → [3] / [4, 1] → [1, 4] / None → [].

    정답표 정답 칸, 웹 입력, 검수 화면의 수정값처럼 형식이 제각각인 답을 한 모양으로 맞춤.
    중복은 없애고(set) 작은 번호부터 정렬함.

    처리 방식:
        1. None → [] (무응답·빈 칸)
        2. 숫자 하나(int, float) → [그 숫자] (예: 3 → [3], 엑셀 숫자 칸 4.0 → [4])
        3. 리스트·튜플 → 중복 제거 후 정렬 (예: [4, 1, 4] → [1, 4])
        4. 그 밖(문자열 등) → 동그라미 숫자(U+2460~U+2464, 1~5번)를 일반 숫자로 바꾼 뒤
           문자열 속 숫자를 모두 뽑음 (예: "1,4" → [1, 4], "3" → [3], 동그라미 4 → [4])
           ※ 동그라미 숫자는 VS Code 글꼴에서 잘 안 보여서 코드에 직접 쓰지 않고 chr(0x2460 + i)로 만듦

    매개변수:
        v: 답 표기 (int / float / list / tuple / str / None)
    반환:
        list[int] 정렬된 선지 번호 목록 (예: [1, 4], 무응답이면 [])
    예외:
        ValueError: 리스트 안에 정수로 못 바꾸는 값이 있을 때
    """
    if v is None:
        return []
    if isinstance(v, (int, float)):
        return [int(v)]
    if isinstance(v, (list, tuple)):
        return sorted({int(x) for x in v})
    # 문자열: 동그라미 숫자 1~5 → "1"~"5"로 바꾼 뒤, 정규식 \d+ 로 숫자 덩어리를 모두 찾아 정수로 바꿈
    #         ("1,4" → ["1", "4"] → [1, 4])
    circled_1_to_5 = "".join(chr(0x2460 + i) for i in range(5))   # U+2460~U+2464 = 동그라미 1~5
    s = str(v).translate(str.maketrans(circled_1_to_5, "12345"))
    return sorted({int(x) for x in re.findall(r"\d+", s)})


# ---------- 파일 입출력 ----------
#   save_answer_key   : AnswerKey → JSON 파일
#   load_answer_key   : JSON 또는 엑셀 → AnswerKey
#   answer_key_template: 빈 정답표 엑셀 만들기
def save_answer_key(key: AnswerKey, path: str) -> None:
    """AnswerKey를 JSON 파일로 저장.

    저장 전에 validate()로 검사하므로 잘못된 정답표는 파일로 남지 않음.
    저장할 폴더가 없으면 만들고, 한글이 그대로 보이게(ensure_ascii=False) 들여쓰기 2칸으로 씀.

    매개변수:
        key: 저장할 정답표
        path: 저장 경로 (예: "storage/exams/EX-.../answer_key.json")
    반환:
        없음
    예외:
        AnswerKeyError: validate() 검사 실패
    """
    key.validate()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)   # 상위 폴더가 없으면 생성
    with open(path, "w", encoding="utf-8") as f:
        json.dump(key.to_dict(), f, ensure_ascii=False, indent=2)


def load_answer_key(path: str, num_choices: int | None = None, exam_id: str = "") -> AnswerKey:
    """JSON 또는 엑셀(.xlsx) 정답표 불러오기. 확장자로 구분.

    매개변수:
        path: 정답표 파일 경로 (대소문자 상관없이 .json 또는 .xlsx로 끝나야 함)
        num_choices: 선지 수 (엑셀일 때만 사용. None이면 엑셀의 '선지 수' 칸, 그것도 없으면 5)
        exam_id: 시험 ID (엑셀일 때만 사용. ""이면 엑셀의 '시험 ID' 칸 값)
                 JSON일 때는 두 값 모두 무시하고 파일 안의 값을 씀
    반환:
        검사를 통과한 AnswerKey
    예외:
        AnswerKeyError: 확장자가 .json/.xlsx가 아님, 또는 내용·형식 오류
    """
    if path.lower().endswith(".json"):
        with open(path, encoding="utf-8") as f:
            return AnswerKey.from_dict(json.load(f))
    if path.lower().endswith(".xlsx"):
        return _load_xlsx(path, num_choices, exam_id)
    raise AnswerKeyError("정답표는 .json 또는 .xlsx 파일이어야 합니다")


def _load_xlsx(path: str, num_choices: int | None, exam_id: str) -> AnswerKey:
    """엑셀 정답표 읽기 (load_answer_key 내부용).

    첫 번째(활성) 시트를 위에서부터 한 줄씩 읽음.
        - '문항' 머리글 행이 나오기 전: A열이 '시험 ID' / '선지 수'인 줄의 B열 값을 meta에 기록
        - 머리글 행 다음부터: A열=문항 번호, B열=정답, C열=배점으로 읽음
          (A열이 비었거나 숫자가 아닌 줄은 건너뜀, C열이 비면 기본 배점 사용)
    문항 수는 읽은 문항 번호 중 가장 큰 값으로 정함.
    셀에 수식이 있으면 data_only=True로 계산된 값을 읽음.

    매개변수:
        path: .xlsx 파일 경로
        num_choices: 선지 수 (None이면 엑셀 '선지 수' 칸 → 그것도 없으면 5)
        exam_id: 시험 ID (""이면 엑셀 '시험 ID' 칸 값)
    반환:
        검사를 통과한 AnswerKey
    예외:
        AnswerKeyError: 시트 없음, 머리글 행 없음, 배점이 숫자가 아님, validate() 실패
    """
    ws = load_workbook(path, data_only=True).active
    if not isinstance(ws, Worksheet):
        raise AnswerKeyError("엑셀 시트가 비어 있습니다")
    meta: dict[str, Any] = {}             # 머리글 위쪽 정보: {"시험 ID": ..., "선지 수": ...}
    answers: dict[int, list[int]] = {}    # 문항 번호 → 정답 선지 목록
    points: dict[int, float] = {}         # 문항 번호 → 배점 (배점 칸이 채워진 문항만)
    header_found = False                  # '문항 | 정답 | 배점' 머리글 행을 지났는지
    for row in ws.iter_rows(values_only=True):
        # 열이 3개보다 적은 줄도 a, b, c를 꺼낼 수 있게 None으로 채움
        cells = list(row) + [None] * 3
        a, b, c = cells[0], cells[1], cells[2]   # A열(문항), B열(정답), C열(배점)
        # 머리글 행 전: 메타 정보만 모으고 다음 줄로
        if not header_found:
            if a == "문항":
                header_found = True
            elif isinstance(a, str) and a.strip() in ("시험 ID", "선지 수"):
                meta[a.strip()] = b
            continue
        # 머리글 행 다음: 문항 번호가 없거나 숫자가 아니면(합계·메모 줄 등) 건너뜀
        if a is None or str(a).strip() == "":
            continue
        try:
            q = int(str(a).strip())
        except ValueError:
            continue
        answers[q] = parse_choices(b)
        if c not in (None, ""):
            try:
                points[q] = float(str(c))
            except ValueError as e:
                raise AnswerKeyError(f"{q}번 배점 '{c}'이(가) 숫자가 아닙니다") from e
    if not header_found:
        raise AnswerKeyError("'문항 | 정답 | 배점' 머리글 행을 찾지 못했습니다")
    # 선지 수: 인자로 받은 값 우선 → 엑셀 '선지 수' 칸 → 기본 5
    k = num_choices or int(meta.get("선지 수") or 5)
    key = AnswerKey(num_questions=max(answers) if answers else 0, num_choices=k,
                    answers=answers, points=points, exam_id=exam_id or str(meta.get("시험 ID") or ""))
    key.validate()
    return key


def answer_key_template(path: str, num_questions: int, num_choices: int, exam_id: str = "",
                        default_points: float = 1) -> str:
    """선생님이 채워 넣을 빈 정답표 엑셀 만들기.
    위쪽 3줄: 시험 ID·선지 수·작성법 / 5번째 줄: '문항 | 정답 | 배점' 머리글 / 그 아래 문항별 행.

    정답 칸(B열)은 비워 두고 배점 칸(C열)은 default_points로 채워 둠.
    배점 칸에는 0 이상의 숫자만 넣을 수 있게 엑셀 데이터 유효성 검사를 걸고,
    스크롤해도 머리글이 보이게 머리글 아래에서 틀 고정함.
    만든 파일은 _load_xlsx()가 그대로 읽을 수 있는 모양임.

    매개변수:
        path: 저장 경로 (예: "storage/exams/EX-.../answer_key_template.xlsx")
        num_questions: 문항 수 (예: 20 → 1~20번 행 생성)
        num_choices: 선지 수 (예: 5, 작성법 안내 문구에도 들어감)
        exam_id: 시험 ID (1행 B열에 기록, 기본 "")
        default_points: 문항별 기본 배점 (기본 1)
    반환:
        저장한 파일 경로 (path 그대로)
    """
    wb = Workbook()
    ws = wb.active
    assert isinstance(ws, Worksheet)
    ws.title = "정답표"
    # 1~3행: 시험 정보와 작성법, 4행: 빈 줄, 5행: 머리글
    ws.append(["시험 ID", exam_id])
    ws.append(["선지 수", num_choices])
    ws.append(["작성법", f"정답 칸에 1~{num_choices} 숫자 입력. 복수 정답 인정은 '1,4'처럼 쉼표로 구분"])
    ws.append([])
    ws.append(["문항", "정답", "배점"])
    head = ws.max_row                     # 머리글 행 번호 (= 5)
    for q in range(1, num_questions + 1):
        ws.append([q, None, default_points])
    # 서식: 머리글 굵게·하늘색 배경·가운데 정렬, 1~3행 항목 이름 굵게, 열 너비
    bold = Font(bold=True)
    for cell in ws[head]:
        cell.font = bold
        cell.fill = PatternFill("solid", fgColor="DDEBF7")
        cell.alignment = Alignment(horizontal="center")
    for r in (1, 2, 3):
        ws.cell(r, 1).font = bold
    ws.column_dimensions["A"].width = 10
    ws.column_dimensions["B"].width = 14
    ws.column_dimensions["C"].width = 10
    # 배점 칸(C열 문항 행 전체)에 "0 이상의 숫자"만 입력되게 데이터 유효성 검사 추가
    dv = DataValidation(type="decimal", operator="greaterThanOrEqual", formula1="0",
                        error="배점은 0 이상의 숫자")
    ws.add_data_validation(dv)
    dv.add(f"C{head + 1}:C{head + num_questions}")
    ws.freeze_panes = f"A{head + 1}"      # 머리글 아래부터 스크롤 (머리글은 항상 보임)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    wb.save(path)
    return path
