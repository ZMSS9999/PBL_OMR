# CheckMate — AI 기반 시험 답안지 자동 채점 시스템

현재 들어 있는 것: **답안지 PDF 생성기 + 양식 좌표 저장 + 웹 입력 화면** (로드맵 2-1),
**ArUco·QR 정렬** (2-2), **규칙 기반 마킹 판독** (2-3), **정답표 입력·채점·엑셀 출력** (2-4),
인쇄 없이 전체 흐름을 검증하는 **가상 촬영 테스트**.

3주차 회의 결정사항을 반영했습니다.
- 객관식 **문항 수 5~45**, **선지 수 2~5** (1 단위로 웹에서 선택)
- 주관식 **5문항 고정** (단답형~한 문장)
- 시험지에는 **이름·학번만** 기입, **시험명·과목명은 QR코드**에 저장
- **세로 / 가로** 양식 두 가지
- JSON 항목명 `template_id`, `template_version`

## 폴더 구조

```
checkmate/
├── frontend/
│   └── index.html            # 답안지 만들기 화면 (HTML+JS, 빌드 없음)
└── backend/
    ├── app/
    │   ├── main.py           # FastAPI: API + frontend 제공
    │   ├── service.py        # 생성·저장 로직 (웹과 분리, 테스트에서도 사용)
    ├── sheetgen/
    │   ├── frame.py          # 방향별 고정 규격 (마커 ID·위치, QR 위치, 좌표계)
    │   ├── layout.py         # 입력값 → 칸 좌표 계산, 입력 범위, QR 내용
    │   └── render_pdf.py     # 좌표 → PDF
    │   └── grading_service.py # 정답표 저장 → 사진 판독 → 검수 수정 → 채점 → 엑셀 (웹·명령행 공용)
    ├── pipeline/
    │   ├── align.py          # 사진 → 마커 검출 → 방향 판별 → 정렬 → QR → 칸 잘라내기
    │   └── read.py           # 규칙 기반 판독: 적응형 이진화 + 채움 비율 → 문항별 답·학번
    ├── grading/
    │   ├── answer_key.py     # 정답표·배점 (JSON / 엑셀 입력, 빈 정답표 양식 만들기)
    │   ├── score.py          # 판독 결과 + 정답표 → 점수 (확정 / 검수 대기 구분)
    │   └── excel.py          # 채점 결과 엑셀 (학생별 점수표, 문항별 결과·정답률, 검수 대기)
    ├── tools/
    │   ├── make_sheet.py     # 웹 없이 명령행으로 답안지 만들기
    │   ├── grade_photos.py   # 웹 없이 명령행으로 채점: 정답표 + 사진 폴더 → 엑셀
    │   ├── demo_end_to_end.py # 가상 학생 답안지로 전체 흐름 시연 + 판독 정확도 확인
    │   └── smoke_test.py     # 가상 촬영 사진으로 정렬·좌표 검증
    ├── fonts/NotoSansKR-subset.ttf   # 한글 글꼴 (SIL OFL)
    ├── storage/              # 생성 결과 (자동 생성, git 제외)
    └── requirements.txt
```

## 실행

- 처음이면 `../checkmate 문서/설명서/VS_CODE_실행가이드.docx`부터 (설치 → 데모 → 웹 화면 순서로 정리)

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

브라우저에서 **http://localhost:8000** 을 열고 값을 고른 뒤 "답안지 PDF 만들기"를 누르면 미리보기와 내려받기가 나옵니다.
API 문서는 http://localhost:8000/docs 에서 볼 수 있습니다.

## 웹 → 백엔드로 전달하는 값

`POST /api/answer-sheets`

```json
{
  "num_questions": 45,          // 필수, 정수 5~45
  "num_choices": 5,             // 필수, 정수 2~5
  "orientation": "portrait",    // "portrait"(세로, 기본) | "landscape"(가로)
  "exam_name": "2026학년도 2학기 중간고사",   // 선택, 30자 이하, QR에만 저장
  "subject": "컴퓨터비전"                     // 선택, 20자 이하, QR에만 저장
}
```

범위 검사는 세 곳에서 합니다: 프론트엔드(입력 제한), FastAPI(pydantic `Field(ge, le)` → 범위 밖이면 422), `SheetSpec.validate()`.
범위 숫자는 `layout.py`의 `NUM_QUESTIONS_RANGE`, `NUM_CHOICES_RANGE` 한 곳에만 있고, 프론트엔드는 `GET /api/sheet-options`로 받아 씁니다.

응답 (201):
```json
{
  "exam_id": "EX-20261001-8C7353",
  "template_id": "P45Q5C", "template_version": "1",
  "pdf_url": "/api/answer-sheets/EX-20261001-8C7353/pdf",
  "template_url": "/api/templates/P45Q5C/1",
  ...
}
```

| API | 설명 |
|---|---|
| `GET /api/sheet-options` | 입력 가능 범위 |
| `POST /api/answer-sheets` | 답안지 생성 |
| `GET /api/answer-sheets/{exam_id}` | 시험 기록 |
| `GET /api/answer-sheets/{exam_id}/pdf` | PDF (`?download=true`면 내려받기) |
| `GET /api/templates/{template_id}/{version}` | 양식 좌표 JSON |

웹 담당이 React로 화면을 새로 만들 때는 위 API를 그대로 호출하면 됩니다. CORS는 `localhost:5173`, `localhost:3000`을 허용해 두었습니다.

## 채점 흐름 (2-3 규칙 기반 판독, 2-4 채점·엑셀)

- 합성 데이터셋(라벨 포함)으로 정확도 평가: `python tools/synth_eval.py --data <synthetic_dataset 폴더> --out <결과 폴더>`
- 모듈끼리 어떻게 이어지는지(A의 출력 → B의 입력)는 `../checkmate 문서/설명서/PIPELINE.docx` 참고. 연결 코드는 `backend/pipeline/flow.py`

```
정답표 입력 → 답안지 사진 올리기 → 정렬·QR 확인 → 규칙 기반 판독 → (애매하면 검수) → 채점 → 엑셀
```

### 명령행으로 (backend 폴더에서)

```bash
# 1) 답안지 만들기 → 출력된 시험 ID 확인
python tools/make_sheet.py --num-questions 20 --num-choices 5 --exam-name "중간고사"
# 2) 빈 정답표 엑셀 만들기 → 열어서 '정답'(예: 3, 복수 정답은 1,4)과 '배점' 채우기
python tools/grade_photos.py --exam-id EX-... --make-key-template
# 3) 정답표 + 사진 폴더 → 채점 결과 엑셀
python tools/grade_photos.py --exam-id EX-... --key 정답표.xlsx --photos ./photos --out 결과.xlsx
```

인쇄·촬영 없이 한 번에 시험해 보려면: `python tools/demo_end_to_end.py --students 15`
(가상 학생 15명의 답안지 사진을 만들어 판독·채점하고 `demo_out/results.xlsx`를 만듭니다.)

### 웹 API로

| API | 설명 |
|---|---|
| `GET /api/exams/{exam_id}/answer-key/template` | 빈 정답표 엑셀 내려받기 |
| `PUT /api/exams/{exam_id}/answer-key` | 정답표 입력 (JSON: `{"answers": {"1": 3, "2": [1,4]}, "points": {"5": 2}}`) |
| `POST /api/exams/{exam_id}/answer-key/upload` | 정답표 파일 올리기 (.xlsx / .json) |
| `POST /api/exams/{exam_id}/sheets` | 답안지 사진 여러 장 올리기 → 정렬·판독 |
| `GET /api/exams/{exam_id}/sheets` / `.../sheets/{sheet_id}` | 판독 결과 목록 / 한 장 전체 |
| `PATCH /api/exams/{exam_id}/sheets/{sheet_id}/review` | 검수 수정 (`{"corrections": {"7": 2}, "student_id": "2026..."}`) |
| `GET /api/exams/{exam_id}/results` / `results.xlsx` | 채점 결과 JSON / 엑셀 |

http://localhost:8000/docs 에서 파일을 직접 올려 시험해 볼 수 있습니다.

### 규칙 기반 판독 (`pipeline/read.py`)

1. 정렬 이미지를 **적응형 이진화** (영역별 밝기 기준으로 흑백 구분)
2. 동그라미 **안쪽 70% 원**에서 검은 픽셀 비율(fill) 계산 → 칸 상태
   - fill ≥ 0.60 → 마킹 / 0.25 ~ 0.60 → **애매** / 0.25 미만 → 빈칸
3. 칸 상태를 모아 문항별 답으로 변환 (3주차 회의 형식)
   - `[3]` 정상, `[2, 5]` 복수 응답, `[]` 무응답, `null` **판정 보류** (애매한 칸 있음 → 검수)
4. 학번도 자리마다 같은 방식. 한 자리라도 정상이 아니면 학번 검수

기준값은 가상 사진으로 정한 임시 값이며, 반칸 채움을 '정상'으로 볼지 정해지기 전까지는 반칸이 검수로 가도록 잡았습니다. 실제 촬영 데이터가 생기면 `ReadConfig`로 다시 정해야 합니다.

### 채점 규칙 (`grading/score.py`)

| 판독 결과 | 채점 |
|---|---|
| 응답 1개, 정답 목록에 있음 | 정답 (배점) |
| 응답 1개, 정답 아님 | 오답 0점 |
| 복수 응답 | 0점 (검수 대기 시트에 '확인 권장'으로 표시) |
| 무응답 | 0점 |
| 판정 보류 | **검수 대기** (점수에 넣지 않음) |

검수 대기 문항이 있거나 학번이 불완전하면 답안지 상태가 **검수 대기**가 되고, 엑셀에서 확정 점수와 따로 표시됩니다
('현재 확정분'과 '최고 가능 점수'). 검수에서 답을 고치면(`review`) 그 답으로 다시 채점되어 **확정**이 됩니다.
정렬에 실패한 사진은 **재촬영 필요**로 남고, QR이 다른 시험인 사진은 이 시험 결과에 저장하지 않습니다.

### 채점 결과 엑셀 시트

1. **학생별 점수표**: 학번, 상태(확정/검수 대기/재촬영 필요), 확정 점수, 최고 가능 점수, 정답·오답 수, 평균·최고·최저
2. **문항별 결과**: 학생 × 문항 표. 읽은 답을 쓰고 색으로 정답/오답/복수/무응답/검수 대기 구분
3. **문항별 정답률**: 정답률(확정 응답 기준)과 선지별 선택 수 (오답 분포)
4. **검수 대기**: 확인할 문항·학번과 칸별 채움 비율
5. **정답표**: 채점에 쓴 정답·배점

주관식 5문항은 5단계(OCR·채점)에서 연결할 예정이라 아직 점수에 들어가지 않습니다.

### 가상 데이터 판독 결과 (`demo_end_to_end.py`, 학생 15명 × 20문항)

| 마킹 유형 | 문항 | 정확 | 검수로 보냄 | 오판독 |
|---|---|---|---|---|
| 진하게 칠함 | 235 | 235 | 0 | 0 |
| 무응답 | 14 | 14 | 0 | 0 |
| 두 칸 칠함 | 13 | 13 | 0 | 0 |
| 반만 옅게 칠함 | 28 | 0 | 28 | 0 |
| 지우고 다시 칠함 | 10 | 10 | 0 | 0 |

'오판독'(틀린 답을 자동 확정)은 0건. 다만 가짜 마킹이라 이 수치는 **코드가 의도대로 동작한다는 확인**이지 실제 성능이 아닙니다.

## 저장 방식

```
storage/templates/P45Q5C_v2.layout.json        ← 양식(칸 배치). 같은 배치는 여러 시험이 공유
storage/exams/EX-20261001-8C7353/exam.json     ← 시험 정보 + 사용한 양식
storage/exams/EX-20261001-8C7353/answer_sheet.pdf
storage/exams/EX-20261001-8C7353/answer_key.json      ← 정답표·배점
storage/exams/EX-20261001-8C7353/sheets/S-XXXXXXXX.json ← 답안지 한 장 판독 결과 (+ 검수 수정)
storage/exams/EX-20261001-8C7353/sheets/S-XXXXXXXX_aligned.jpg ← 정렬 이미지 (검수 화면용)
storage/exams/EX-20261001-8C7353/results.xlsx         ← 채점 결과 엑셀
```

- 칸 위치·크기는 **팀 양식**(pptx `PDF 생성기 양식`, 좌표 JSON `json양식`)을 그대로 따릅니다 (`TEMPLATE_VERSION` 2). 45문항·5지선다면 마커·객관식 동그라미·서술형 칸이 팀 JSON과 같고, `python tools/check_team_form.py`로 확인합니다. 팀 JSON 사본은 `backend/tools/team_form/`.
- **template_id**는 입력값에서 바로 만듭니다: `P45Q5C` = 세로(P)·45문항·5지선다. 가로는 `L`.
- **template_version**은 `layout.py`의 `TEMPLATE_VERSION`. 칸 배치 코드를 바꾸면 올려야 합니다.
  저장된 양식과 같은 id·version인데 좌표가 다르면(`geometry_hash` 불일치) 생성을 멈추고 알려 줍니다. 옛 인쇄본에 새 좌표가 잘못 쓰이는 일을 막기 위해서입니다.
- 좌표는 **mm, 용지 왼쪽 위 원점**. 정렬 이미지 기준 px는 200dpi(세로 1654×2339, 가로 2339×1654)이고, `bbox_to_px()`로 변환합니다.

양식 JSON 주요 항목: `frame`(마커·QR 위치), `name_box`, `student_id.bubbles[]`(digit_index, value),
`mcq.questions[].choices[]`(q, choice, center_mm, radius_mm, bbox_mm), `short_answers.items[]`(no, answer_bbox_mm = OCR 영역).

## QR 코드 내용

```
CM1|<exam_id>|<template_id>|<template_version>|<과목명>|<시험명>
예) CM1|EX-20261001-8C7353|P45Q5C|2|컴퓨터비전|2026학년도 2학기 중간고사
```
그래서 시험명·과목명에는 `|`를 쓸 수 없습니다.

## 정렬 순서 (`pipeline/align.py`)

1. ArUco 검출 (ID 0~3, 서브픽셀 코너 보정). 마커 세트 2가지를 다 찾음 (`frame.MARKER_SETS`)
   - `team`: 팀 좌표 JSON·합성 데이터셋·우리 PDF 생성기 (`DICT_6X6_50`, TL0 TR1 BL2 BR3, 18.249mm)
   - `print`: 실제 인쇄해서 쓴 답안지 (`OMR_A4_가로형_수정본_인쇄.pptx`, `DICT_4X4_50`, TL0 TR1 BR2 BL3, 16mm). 세로 인쇄본 값은 실측 사진이 없어 미확인
2. **방향·마커 세트 판별**: 세트·방향 조합마다 변환을 구해 보고 마커 모양이 가장 잘 맞는 쪽을 고름
3. 코너 12~16점으로 호모그래피 계산 (RANSAC). 마커 3개만 보여도 정렬, 2개 이하면 재촬영 안내
4. 정면 이미지로 펴기 (200dpi)
5. 정렬된 이미지의 QR 고정 위치에서 QR 읽기 → template_id·version으로 양식 JSON 불러오기
6. 양식 좌표로 객관식·학번 버블, 주관식 답 칸 잘라내기
7. 판독 전 칸 위치 미세 보정 (`read.refine_offsets`): 객관식 열·학번 칸마다 인쇄된 동그라미 테두리를 찾아 ±2.5mm 안에서 맞춤. 인쇄본은 객관식 칸이 팀 JSON보다 약 1.7mm 왼쪽이라 필요함

## 가상 촬영 테스트 결과 (실제 사진 아님)

```bash
cd backend
python tools/smoke_test.py --orientation portrait --num-questions 45 --num-choices 5 --trials 10
python tools/smoke_test.py --orientation landscape --num-questions 45 --num-choices 5 --trials 10
```

팀 양식 반영 후(2026-10-14, 난이도 easy·medium·hard 각 10장):

| 양식 | 정렬 성공 | 방향·QR 인식 | 칸 중심 오차 평균 / 최대 | 객관식 판독 |
|---|---|---|---|---|
| P45Q5C (세로) | 30/30 | 30/30 | 0.08~0.10 / 0.45 mm | 100% |
| L45Q5C (가로) | 30/30 | 30/30 | 0.08~0.13 / 0.37 mm | 100% |

팀 양식은 마커가 용지 끝에서 4mm 안쪽이라, 사진을 종이에 꽉 차게 찍으면 마커가 화면 밖으로 잘리기 쉽습니다(데모의 hard 사진 12장 중 1장). 마커 4개가 다 보이게 여백을 두고 찍어야 합니다.
가상 사진에는 종이 휨·구김·렌즈 왜곡이 없으므로, 실제 인쇄·촬영 검증은 예산이 들어온 뒤 필요합니다.

## VS Code에서 빨간 줄이 뜰 때

- `checkmate` 폴더를 VS Code로 열면 `pyrightconfig.json`이 `backend`를 import 경로에 넣어 줍니다 (`from sheetgen ...` 인식).
- 실행은 `backend` 폴더에서 합니다 (`cd backend`). 다른 폴더에서 실행하면 `ModuleNotFoundError: sheetgen`이 납니다.
