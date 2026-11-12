# pyright: basic
"""전체 흐름 시연 + 판독 성능 확인 (인쇄·촬영 없이).

[하는 일]
  답안지 PDF 생성 → 가상 학생 N명 마킹 → 가상 스마트폰 사진 → 정렬 → 규칙 기반 판독
  → 정답표 입력 → 채점 → 엑셀 저장
  프린터·스마트폰 없이 컴퓨터 안에서 P1~P5 전체가 돌아가는지 한 번에 확인하는 시연용 스크립트.
  처음 설치한 뒤 "제대로 동작하나?"를 볼 때 가장 먼저 돌려 보는 명령 (checkmate 문서/설명서/VS_CODE_실행가이드 2번).

2-3단계 4번: '학생이 실제 표시한 답(라벨)'과 판독 결과를 비교함.
(시험 문제를 맞혔는지가 아니라, 표시한 답을 정확히 읽었는지 확인)
  라벨 = 가상 학생에게 "이 칸을 칠해라"라고 시킨 답. 직접 만든 마킹이라 정답을 미리 알고 있음.

마킹 유형 (가상)
  normal  진하게 칠함              라벨 [a]
  blank   아무것도 안 함           라벨 []
  double  두 칸을 칠함             라벨 [a, b]
  partial 반쯤만 옅게 칠함         라벨 [a]   ← 규칙 판독은 '애매 → 검수'로 보내는 게 목표
  erased  한 칸을 지우고 다른 칸 칠함 라벨 [a] (지운 칸은 표시 아님)

판정 구분
  정확   : 자동 확정했고 라벨과 같음
  검수   : 판정 보류로 사람에게 넘김 (틀리게 확정하지 않았으므로 안전)
  오판독 : 자동 확정했는데 라벨과 다름  ← 가장 위험, 0에 가까워야 함

사용:
  (backend 폴더에서) python tools/demo_end_to_end.py --students 12 --num-questions 20 --num-choices 5

[명령행 옵션] (모두 선택, 괄호 안은 기본값)
  --students      : 가상 학생 수 = 만들 답안지 사진 수 (12)
  --num-questions : 객관식 문항 수 (20, 범위 5~45)
  --num-choices   : 선지 수 (5, 범위 2~5)
  --orientation   : 용지 방향 portrait(세로) / landscape(가로) (portrait)
  --levels        : 학생마다 돌아가며 적용할 촬영 난이도, 쉼표로 구분 (easy,medium,hard)
                    난이도 내용은 smoke_test.py의 LEVELS (기울기·회전·조명·흐림·노이즈·JPEG 화질)
  --seed          : 난수 시드 (0). 같은 값이면 매번 같은 가상 마킹·사진이 나옴 (결과 재현용)
  --out           : 결과 폴더 (demo_out)

[결과 확인]
  터미널 출력
    1. 첫 줄 "시험 EX-...  양식 P20Q5C  (20문항 5지선다)" → 이번에 만든 시험
    2. 마킹 유형별 표: 유형 / 문항 수 / 정확 / 검수 / 오판독 / 자동확정 정확도
       → 오판독이 0이면 정상. 자동확정 정확도 = 정확 ÷ (정확 + 오판독) (검수로 보낸 문항은 빼고 계산)
    3. "학번: {'정확': 11, '검수': 1}" → 학번 판독 결과 (정렬에 실패한 장은 '정렬 실패')
    4. "채점 결과 엑셀: ..." / "빈 정답표 양식: ..." → 만들어진 파일 경로
  생성 파일
    <out>/photos/student01_easy.jpg ...  ← 가상 촬영 사진 (파일명 = 학생 번호_난이도)
    <out>/results.xlsx                   ← 채점 결과 엑셀 (시트 5개: 학생별 점수, 문항별 결과, 정답률, 검수 대기, 정답표)
    <out>/labels.json                    ← 라벨(정답)과 집계표 (나중에 다시 분석할 때 사용)
    storage/exams/<시험ID>/...           ← 웹과 같은 방식으로 저장된 시험·정답표·판독 결과
                                            (웹 API로 이어서 검수해 볼 수 있음)

[내부 처리 순서] (main 함수의 1)~5) 주석과 같음)
  1) 답안지 생성 → PDF를 이미지로 변환 (smoke_test.pdf_to_bgr)
  2) 무작위 정답표 입력 (5의 배수 문항 2점, 마지막 문항은 복수 정답)
  3) 학생마다 가상 마킹(mark_student) → 가상 촬영(smoke_test.simulate_photo) → P1·P2·P3 판독·저장
  4) 판독 결과와 라벨 비교 → 표 출력
  5) P4 채점 → P5 엑셀, labels.json 저장

[다른 파일과의 관계]
  - tools/smoke_test.py    : 가상 촬영(simulate_photo), 난이도(LEVELS), PDF→이미지(pdf_to_bgr)를 가져다 씀
  - app/service.py         : 답안지 생성 (웹과 같은 함수)
  - app/grading_service.py : 정답표 저장, 사진 판독·저장, 채점·엑셀 (웹과 같은 함수)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict

import cv2
import numpy as np

# import 경로 추가: backend 폴더(app, sheetgen 등)와 tools 폴더(같은 폴더의 smoke_test.py)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from app import grading_service as gs  # noqa: E402
from app import service  # noqa: E402
from sheetgen import frame as F  # noqa: E402
from sheetgen.layout import mm_to_px  # noqa: E402
from smoke_test import LEVELS, pdf_to_bgr, simulate_photo  # noqa: E402

DPI = F.CANONICAL_DPI   # 기준 이미지 해상도 (200dpi). PDF를 이 해상도로 그림 → mm 좌표를 px로 바꿀 때 사용
# 마킹 유형별 출현 비율 (합 1.0). 문항마다 이 비율로 유형을 무작위로 고름 (예: 80%는 진하게 칠함)
MARK_TYPES = {"normal": 0.80, "blank": 0.04, "double": 0.04, "partial": 0.07, "erased": 0.05}


class Marker:
    """답안지 이미지 위에 가상 학생의 펜 자국(마킹)을 그리는 도구.

    사람이 칠한 것처럼 보이도록 위치·크기·진하기에 약간의 무작위 흔들림을 넣음.
    그리는 대상 이미지(img)를 직접 수정함 (복사본을 넘겨받아 쓰는 구조).
    """

    def __init__(self, img: np.ndarray, rng: np.random.Generator):
        """그릴 이미지와 난수 생성기를 저장.

        매개변수:
            img: 마킹을 그릴 답안지 이미지 (BGR 배열, 200dpi). 이 배열이 직접 바뀜
            rng: 난수 생성기 (np.random.default_rng(시드)). 같은 시드면 같은 마킹
        """
        self.img, self.rng = img, rng

    def _px(self, v: float) -> float:
        """mm 길이·좌표 → px 값 (200dpi 기준).

        매개변수:
            v: mm 단위 값 (예: 10.0)
        반환:
            px 단위 실수 (예: 10.0mm → 약 78.7)
        """
        return mm_to_px(v, DPI)

    def fill(self, b: dict, tone=None, scale=1.0):
        """동그라미 하나를 펜으로 꽉 칠한 것처럼 그림 (타원으로 채움).

        중심을 표준편차 0.25mm만큼 흔들고, 크기는 반지름의 80~105%, 모양은 약간 찌그러진 타원으로
        만들어 사람 손으로 칠한 느낌을 냄.

        매개변수:
            b: 양식의 동그라미 정보 dict. "center_mm"([x, y] mm), "radius_mm"(mm) 사용
            tone: 칠할 밝기 0~255 (0=검정). None이면 15~70 사이 무작위 (진한 펜)
            scale: 크기 배율 (기본 1.0. 지운 흔적은 1.05로 조금 크게)
        반환:
            None (self.img를 직접 수정)
        """
        rng = self.rng
        cx = self._px(b["center_mm"][0] + rng.normal(0, 0.25))
        cy = self._px(b["center_mm"][1] + rng.normal(0, 0.25))
        rr = self._px(b["radius_mm"]) * rng.uniform(0.8, 1.05) * scale
        t = int(tone if tone is not None else rng.uniform(15, 70))
        cv2.ellipse(self.img, (int(cx), int(cy)), (int(rr), int(rr * rng.uniform(0.85, 1.0))),
                    rng.uniform(0, 180), 0, 360, (t, t, t), -1, cv2.LINE_AA)

    def partial(self, b: dict):
        """동그라미의 절반 정도만 옅게 칠함.

        170도 부채꼴을 밝기 70~120(회색)으로 칠함 → 채움 비율이 애매 구간에 들어가서
        규칙 판독이 '판정 보류(검수)'로 보내는지 확인하려는 용도.

        매개변수:
            b: 양식의 동그라미 정보 dict ("center_mm", "radius_mm" 사용)
        반환:
            None (self.img를 직접 수정)
        """
        cx, cy = (int(self._px(v)) for v in b["center_mm"])
        r = int(self._px(b["radius_mm"]))
        t = int(self.rng.uniform(70, 120))
        start = self.rng.uniform(0, 360)
        cv2.ellipse(self.img, (cx, cy), (r, r), 0, start, start + 170, (t, t, t), -1, cv2.LINE_AA)

    def erased(self, b: dict):
        """지우개로 지운 흔적: 아주 옅은 회색 얼룩.

        fill()을 밝기 205~225(거의 흰색)로 조금 크게 칠함 → 판독기가 마킹으로 잘못 읽지 않는지 확인용.

        매개변수:
            b: 양식의 동그라미 정보 dict ("center_mm", "radius_mm" 사용)
        반환:
            None (self.img를 직접 수정)
        """
        self.fill(b, tone=self.rng.uniform(205, 225), scale=1.05)


def mark_student(page: np.ndarray, layout: dict, rng: np.random.Generator):
    """가상 학생 한 명의 답안지 만들기: 빈 답안지 이미지에 객관식 답과 학번을 마킹.

    문항마다 MARK_TYPES 비율로 마킹 유형(normal/blank/double/partial/erased)을 고르고,
    무작위 선지를 칠한 뒤 '학생이 표시하려던 답(라벨)'을 기록함.
    학번은 자리마다 0~9 중 무작위 숫자 하나를 칠함.

    매개변수:
        page: 빈 답안지 이미지 (BGR 배열, 200dpi). 원본은 바뀌지 않음 (복사해서 그림)
        layout: 양식 좌표 dict (layout.json 내용). mcq.questions, student_id.bubbles 사용
        rng: 난수 생성기
    반환:
        (img, labels, types, student_id) 4개 묶음
          img: 마킹된 답안지 이미지 (BGR 배열)
          labels: {문항 번호: 라벨 답 목록} (예: {1: [3], 2: [], 3: [2, 5]})
          types: {문항 번호: 마킹 유형} (예: {1: "normal", 2: "blank"})
          student_id: 칠한 학번 문자열 (예: "2026123456")
    """
    img = page.copy()
    m = Marker(img, rng)
    labels, types = {}, {}   # 문항별 라벨 답, 문항별 마킹 유형
    names, probs = list(MARK_TYPES), np.array(list(MARK_TYPES.values()))   # 유형 이름 목록, 출현 비율 배열
    for q in layout["mcq"]["questions"]:
        ch = q["choices"]
        k = len(ch)                                          # 선지 수
        t = str(rng.choice(names, p=probs / probs.sum()))   # 이 문항의 마킹 유형
        a = int(rng.integers(1, k + 1))                      # 학생이 고른 답 (1~k)
        # 유형별로 그리기 + 라벨 기록 (double·erased는 a가 아닌 다른 선지 b를 하나 더 고름)
        if t == "normal":
            m.fill(ch[a - 1]); labels[q["q"]] = [a]
        elif t == "blank":
            labels[q["q"]] = []
        elif t == "double":
            b = int(rng.choice([c for c in range(1, k + 1) if c != a]))
            m.fill(ch[a - 1]); m.fill(ch[b - 1]); labels[q["q"]] = sorted([a, b])
        elif t == "partial":
            m.partial(ch[a - 1]); labels[q["q"]] = [a]
        elif t == "erased":
            b = int(rng.choice([c for c in range(1, k + 1) if c != a]))
            m.erased(ch[b - 1]); m.fill(ch[a - 1]); labels[q["q"]] = [a]
        types[q["q"]] = t
    # 학번: 자리 수만큼 0~9 무작위 숫자 → 해당 숫자 칸만 칠함
    digits = [int(d) for d in rng.integers(0, 10, layout["student_id"]["digits"])]
    for b in layout["student_id"]["bubbles"]:
        if b["value"] == digits[b["digit_index"]]:
            m.fill(b)
    return img, labels, types, "".join(map(str, digits))


def main():
    """명령행 옵션을 읽어 답안지 생성부터 엑셀 저장까지 전체 시연을 실행.

    단계는 모듈 설명 [내부 처리 순서]와 아래 1)~5) 주석 참고.

    매개변수:
        없음 (명령행 옵션을 sys.argv에서 읽음)
    반환:
        None. 결과는 터미널 표와 --out 폴더의 파일로 나옴
    예외:
        SystemExit: PDF를 이미지로 바꿀 도구(poppler/pypdfium2)가 없을 때 (smoke_test.pdf_to_bgr)
        AssertionError: 방금 만든 양식·PDF 파일을 찾지 못했을 때 (정상이라면 일어나지 않음)
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--students", type=int, default=12)
    ap.add_argument("--num-questions", type=int, default=20)
    ap.add_argument("--num-choices", type=int, default=5)
    ap.add_argument("--orientation", default="portrait", choices=["portrait", "landscape"])
    ap.add_argument("--levels", default="easy,medium,hard", help="학생마다 돌아가며 적용할 촬영 난이도")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="demo_out")
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)   # 난수 생성기 (마킹·사진 왜곡·정답표 모두 여기서 뽑음)
    os.makedirs(os.path.join(a.out, "photos"), exist_ok=True)

    # 1) 답안지 생성 (웹과 같은 함수)
    exam = service.create_answer_sheet(a.num_questions, a.num_choices, a.orientation, "데모 중간고사", "컴퓨터비전")
    exam_id = exam["exam_id"]
    layout = service.load_template(exam["template_id"], exam["template_version"])
    pdf = service.exam_pdf_path(exam_id)
    assert layout is not None and pdf is not None
    page = pdf_to_bgr(pdf)   # 빈 답안지 이미지 (200dpi, 마킹 전)
    print(f"시험 {exam_id}  양식 {exam['template_id']}  ({a.num_questions}문항 {a.num_choices}지선다)")

    # 2) 정답표 입력 (5의 배수 문항은 2점, 나머지 1점 / 마지막 문항은 복수 정답 인정 예시)
    # key_ans: {문항: [정답 선지]} 무작위. 마지막 문항만 정답 선지와 그다음 선지 둘 다 인정
    # points: 5, 10, 15 ... 번 문항만 2점 (나머지는 기본 1점)
    key_ans = {q: [int(rng.integers(1, a.num_choices + 1))] for q in range(1, a.num_questions + 1)}
    key_ans[a.num_questions] = sorted({key_ans[a.num_questions][0], (key_ans[a.num_questions][0] % a.num_choices) + 1})
    points = {q: 2 for q in range(5, a.num_questions + 1, 5)}
    gs.set_answer_key(exam_id, key_ans, points)
    gs.make_answer_key_template(exam_id)   # 빈 정답표 엑셀도 만들어 둠 (양식 확인용, 마지막에 경로 출력)

    # 3) 학생 답안지 사진 → 판독
    # 학생 s마다: 마킹 → 난이도(levels를 차례로 돌림)에 맞춰 가상 촬영 → 사진 저장 → P1·P2·P3 판독·저장
    levels = a.levels.split(",")
    truth = {}   # {사진 파일명: {"labels", "types", "student_id", "sheet_id"}} = 비교용 정답 기록
    for s in range(a.students):
        marked, labels, types, sid = mark_student(page, layout, rng)
        lv = levels[s % len(levels)]
        photo, _ = simulate_photo(marked, LEVELS[lv], rng)
        name = f"student{s + 1:02d}_{lv}.jpg"
        cv2.imwrite(os.path.join(a.out, "photos", name), photo, [cv2.IMWRITE_JPEG_QUALITY, 90])
        rec = gs.process_photo(exam_id, photo, name)
        truth[name] = {"labels": labels, "types": types, "student_id": sid, "sheet_id": rec["sheet_id"]}

    # 4) 판독 결과 vs 라벨
    # sheets: {사진 파일명: 저장된 판독 기록}
    # tally: {마킹 유형 또는 "전체": Counter(정확/검수/오판독 개수)}, sid_tally: 학번 판독 결과 개수
    # 판정: 답이 None(판정 보류) → 검수, 라벨과 같음 → 정확, 다름 → 오판독
    sheets = {r["source"]: r for r in gs.list_sheets(exam_id)}
    tally: dict[str, Counter] = defaultdict(Counter)
    sid_tally = Counter()
    for name, t in truth.items():
        rec = sheets[name]
        if rec["status"] != "read":
            sid_tally["정렬 실패"] += 1
            continue
        r = rec["read"]
        sid_read = r["student_id"]["text"]
        sid_tally["정확" if sid_read == t["student_id"] else "검수" if sid_read is None else "오판독"] += 1
        for q in r["mcq"]:
            lab = t["labels"][q["q"]]
            res = "검수" if q["answer"] is None else ("정확" if q["answer"] == lab else "오판독")
            tally[t["types"][q["q"]]][res] += 1
            tally["전체"][res] += 1

    # 유형별 결과 표 출력. 자동확정 정확도 = 정확 ÷ (정확 + 오판독), 자동 확정한 문항이 없으면 "-"
    print(f"\n판독 결과 vs 라벨 (문항 단위, 학생 {a.students}명)")
    print(f"{'유형':8} {'문항':>5} {'정확':>5} {'검수':>5} {'오판독':>6}  {'자동확정 정확도':>12}")
    for t in list(MARK_TYPES) + ["전체"]:
        c = tally[t]
        n = sum(c.values())
        auto = c["정확"] + c["오판독"]
        acc = f"{c['정확'] / auto:.3f}" if auto else "-"
        print(f"{t:8} {n:5} {c['정확']:5} {c['검수']:5} {c['오판독']:6}  {acc:>12}")
    print(f"학번: {dict(sid_tally)}")

    # 5) 채점 → 엑셀
    # 저장된 답안지 전체 P4 채점 → P5 엑셀, 라벨·집계는 labels.json으로 저장
    out = gs.export_results(exam_id, os.path.join(a.out, "results.xlsx"))
    with open(os.path.join(a.out, "labels.json"), "w", encoding="utf-8") as f:
        json.dump({"exam_id": exam_id, "truth": truth, "tally": {k: dict(v) for k, v in tally.items()},
                   "student_id": dict(sid_tally)}, f, ensure_ascii=False, indent=1)
    print(f"\n채점 결과 엑셀: {out}")
    print(f"빈 정답표 양식: {os.path.join(service.EXAM_DIR, exam_id, 'answer_key_template.xlsx')}")


if __name__ == "__main__":
    main()
