# pyright: basic
"""파이프라인 한 번에 돌리기 — 모듈들이 어떻게 이어지는지 보여 주는 가장 짧은 예시.

[하는 일]
  이미 만들어 둔 시험(답안지)의 사진 폴더를 정답표와 함께 넣으면
  사진마다 P1 정렬 → P2 시험 확인 → P3 판독 → P4 채점을 하고, 모아서 P5 엑셀을 만듦.
  pipeline/flow.py의 run_batch() 한 줄로 P1~P5가 모두 돌아가는 모습을 보여 주는 것이 목적.

[실행 예] (backend 폴더에서)
  python tools/run_pipeline.py --exam-id EX-... --key 정답표.xlsx --photos ./photos --out 결과.xlsx

[명령행 옵션]
  --exam-id : 시험 ID (필수). 답안지를 만들 때 나온 값 (예: EX-20261012-ABC123)
  --key     : 정답표 파일 (필수, .xlsx 또는 .json)
  --photos  : 답안지 사진 폴더 (필수). IMG_EXT 확장자 파일만 이름순으로 처리
  --out     : 결과 엑셀 경로 (기본: 실행한 폴더의 pipeline_results.xlsx)

grade_photos.py와 결과는 같음. 차이점:
  - grade_photos.py : 판독 결과를 storage/에 저장 (나중에 웹에서 검수·재채점 가능)
  - 이 파일         : 저장 없이 메모리에서 P1~P5를 한 번에 → 실험·디버깅용
                      (정답표도 storage/에 저장하지 않음. 결과 엑셀만 --out 경로에 생김)

[결과 확인]
  터미널에 사진마다 한 줄
    student01.jpg: 학번 2026123456  확정  점수 18/20
      → 학번(못 읽었으면 '?'가 섞인 raw 값), 상태(확정 / 검수 대기), 확정 점수 / 만점
    student02.jpg: align_failed - 사유
      → 정렬 실패(재촬영 필요) 또는 wrong_exam(다른 시험 답안지)과 그 이유
  마지막 줄 "엑셀: <경로>" → 결과 엑셀 (학생별 점수표 등). 정렬 실패는 '재촬영 필요' 행으로 들어가고
  다른 시험 답안지는 엑셀에서 빠짐.

[데이터가 흘러가는 순서]
  exam.json, layout.json ──┐
  정답표(.xlsx/.json) ─────┼─► flow.run_batch(사진들)
  사진 폴더 ───────────────┘      사진마다: P1 정렬 → P2 시험 확인 → P3 판독 → P4 채점
                                  모아서 : P5 엑셀

[다른 파일과의 관계]
  - pipeline/flow.py   : P1~P5 단계 함수와 이를 잇는 run_batch() (실제 처리)
  - app/service.py     : 시험 기록(exam.json)과 양식 좌표(layout.json) 읽기
  - grading/           : 정답표 읽기(load_answer_key)
  - 자세한 단계별 입력·출력은 checkmate 문서/설명서/PIPELINE 참고
"""
import argparse
import glob
import os
import sys

import cv2

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))   # backend 폴더를 import 경로에 추가
from app import service  # noqa: E402
from grading import load_answer_key  # noqa: E402
from pipeline import flow  # noqa: E402

# 사진으로 인정하는 파일 확장자 (소문자로 바꿔서 비교하므로 .JPG도 포함됨)
IMG_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff")


def main():
    """명령행 옵션을 읽어 시험·양식·정답표·사진을 준비하고 flow.run_batch()로 P1~P5를 실행.

    결과 엑셀을 만들고, 사진마다 채점 요약 한 줄을 터미널에 출력함.
    아무것도 storage/에 저장하지 않음 (결과 엑셀 한 개만 생성).

    매개변수:
        없음 (명령행 옵션을 sys.argv에서 읽음)
    반환:
        None. 결과는 터미널 출력과 --out 엑셀 파일로 나옴
    예외:
        SystemExit: 시험 ID나 양식 파일이 없을 때 "[오류] ..." 메시지로 종료
        AnswerKeyError(ValueError의 한 종류): 정답표 파일 형식·내용 오류
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--exam-id", required=True)
    ap.add_argument("--key", required=True, help="정답표 (.xlsx 또는 .json)")
    ap.add_argument("--photos", required=True, help="답안지 사진 폴더")
    ap.add_argument("--out", default="pipeline_results.xlsx")
    a = ap.parse_args()

    # (준비) 시험 기록과 양식 좌표 불러오기 — 답안지 생성 단계의 출력물
    # exam: exam.json 내용 dict (문항 수·선지 수·양식 이름 등), layout: 칸 좌표 dict (layout.json)
    exam = service.get_exam(a.exam_id)
    if not exam:
        raise SystemExit(f"[오류] 시험 {a.exam_id} 없음 (backend/storage/exams 확인)")
    layout = service.load_template(exam["template_id"], exam["template_version"])
    if layout is None:
        raise SystemExit(f"[오류] 양식 {exam['template_id']} 파일 없음")

    # P4의 입력: 정답표 (key: AnswerKey 객체. 엑셀이면 선지 수·시험 ID는 시험 기록 값으로 채움)
    key = load_answer_key(a.key, num_choices=exam["num_choices"], exam_id=a.exam_id)

    # P1의 입력: (파일명, 이미지) 묶음. 제너레이터라 사진을 한 장씩만 메모리에 올림
    # 읽을 수 없는 파일(cv2.imread가 None)은 조용히 건너뜀
    paths = sorted(p for p in glob.glob(os.path.join(a.photos, "*")) if p.lower().endswith(IMG_EXT))
    photos = ((os.path.basename(p), img) for p in paths if (img := cv2.imread(p)) is not None)

    # P1~P5 한 번에
    # results: 사진별 SheetResult 목록 (status, error, grade 등), xlsx: 만들어진 엑셀 경로
    results, xlsx = flow.run_batch(photos, exam, layout, key, a.out)

    # 사진별 요약 출력
    for r in results:
        if r.status == flow.READ and r.grade:
            # 판독·채점 성공: 학번(확정 못 했으면 raw), 상태(확정/검수 대기), 확정 점수/만점
            g = r.grade
            print(f"  {r.source}: 학번 {g['student_id'] or g['student_id_raw']}  {g['status']}  "
                  f"점수 {g['confirmed_score']:g}/{g['total_points']:g}")
        else:
            # 정렬 실패(align_failed) 또는 다른 시험(wrong_exam): 상태와 사유
            print(f"  {r.source}: {r.status} - {r.error}")
    print("엑셀:", xlsx)


if __name__ == "__main__":
    main()
