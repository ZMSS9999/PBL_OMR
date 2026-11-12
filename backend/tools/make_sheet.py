# pyright: basic
"""웹 없이 명령행으로 답안지 PDF 만들기 (테스트용).

[하는 일]
  웹 화면의 "답안지 PDF 만들기" 버튼과 똑같은 일을 터미널에서 함.
  웹과 같은 함수 app/service.py의 create_answer_sheet()를 호출하므로 결과물도 웹과 완전히 같음.
  서버(uvicorn)를 켜지 않고 답안지만 빨리 만들어 보고 싶을 때, 또는 채점 테스트용 시험을 만들 때 사용.

[실행 예] (backend 폴더에서)
  python tools/make_sheet.py --num-questions 45 --num-choices 5 --orientation portrait \
      --exam-name "2026학년도 2학기 중간고사" --subject "컴퓨터비전"
  python tools/make_sheet.py --num-questions 20 --num-choices 5          ← 필수 옵션만 쓴 가장 짧은 형태

[명령행 옵션]
  --num-questions : 객관식 문항 수 (필수, 정수 5~45)
  --num-choices   : 선지 수 = 몇 지선다인지 (필수, 정수 2~5)
  --orientation   : 용지 방향. portrait(세로, 기본값) / landscape(가로)
  --exam-name     : 시험명 (선택, 30자 이하). 종이에는 안 찍히고 QR코드에만 저장됨
  --subject       : 과목명 (선택, 20자 이하). 시험명과 마찬가지로 QR코드에만 저장됨
  ※ 범위를 벗어나거나 시험명·과목명에 '|' 문자가 있으면 "[오류] ..." 메시지를 내고 종료

[결과 확인]
  터미널에 4줄 출력
    시험 ID : EX-20261013-8C7353        ← 채점할 때(grade_photos.py 등) --exam-id로 넣을 값
    양식    : P45Q5C v2                  ← 양식 이름(P=세로/L=가로, 45문항, 5지선다)과 버전
    PDF     : .../storage/exams/<시험ID>/answer_sheet.pdf          ← 인쇄할 답안지
    좌표    : .../storage/templates/P45Q5C_v1.layout.json          ← 칸 좌표 JSON (판독 때 사용)
  같은 폴더에 exam.json(시험 정보 기록)도 함께 생김.

[내부 처리 순서]
  1. 명령행 옵션 읽기 (argparse)
  2. service.create_answer_sheet() 호출
     → 입력 검사 → 양식 좌표 계산·저장 → 시험 ID 발급 → QR 내용 → PDF → exam.json
  3. 만들어진 파일 경로 출력

[다른 파일과의 관계]
  - app/service.py : 실제 생성 로직 (이 파일은 그 함수를 부르기만 함)
  - 다음 단계      : tools/grade_photos.py 로 정답표 입력·사진 채점
  - 함수 없이 위에서 아래로 바로 실행되는 짧은 스크립트임
"""
import argparse
import os
import sys

# 이 파일은 backend/tools/ 안에 있으므로, 한 단계 위(backend 폴더)를 import 경로에 추가
# → "from app import service"처럼 backend 안의 패키지를 불러올 수 있게 함
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app import service  # noqa: E402

# 명령행 옵션 정의 (각 옵션의 의미는 위 모듈 설명 [명령행 옵션] 참고)
ap = argparse.ArgumentParser()
ap.add_argument("--num-questions", type=int, required=True)
ap.add_argument("--num-choices", type=int, required=True)
ap.add_argument("--orientation", default="portrait", choices=["portrait", "landscape"])
ap.add_argument("--exam-name", default="")
ap.add_argument("--subject", default="")
a = ap.parse_args()   # a.num_questions, a.num_choices, a.orientation, a.exam_name, a.subject 로 꺼내 씀
try:
    # rec = 시험 기록 dict (exam.json 내용과 같음. exam_id, template_id, template_version 등)
    rec = service.create_answer_sheet(a.num_questions, a.num_choices, a.orientation, a.exam_name, a.subject)
except ValueError as e:
    # 입력 범위 위반 등 → 긴 오류 추적(traceback) 대신 한 줄 메시지만 보이고 종료
    raise SystemExit(f"[오류] {e}")
# 결과 출력: 시험 ID·양식 이름, PDF 경로, 좌표 JSON 경로
print(f"시험 ID : {rec['exam_id']}\n양식    : {rec['template_id']} v{rec['template_version']}")
print(f"PDF     : {service.exam_pdf_path(rec['exam_id'])}")
print(f"좌표    : {service.template_path(rec['template_id'], rec['template_version'])}")
