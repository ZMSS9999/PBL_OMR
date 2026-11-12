# pyright: basic
"""웹 없이 명령행으로 채점하기: 정답표 + 답안지 사진 폴더 → 채점 결과 엑셀.

[하는 일]
  웹 API와 같은 app/grading_service.py 함수를 호출해서 사진 여러 장을 한 번에 채점함.
  판독 결과가 storage/에 저장되므로, 나중에 웹 화면(API)에서 이어서 검수·재채점할 수 있음.
  (저장 없이 메모리에서만 돌리는 간단한 버전은 tools/run_pipeline.py)

[실행 예] (backend 폴더에서)
  # 1) 빈 정답표 엑셀 만들기 → 열어서 정답·배점 채우기
  python tools/grade_photos.py --exam-id EX-20261012-ABC123 --make-key-template
  # 2) 정답표 + 사진 폴더로 채점 → 엑셀
  python tools/grade_photos.py --exam-id EX-20261012-ABC123 --key 정답표.xlsx --photos ./photos --out 결과.xlsx
  # 3) 사진만 더 추가 (정답표는 이미 저장된 것 사용)
  python tools/grade_photos.py --exam-id EX-20261012-ABC123 --photos ./photos_추가분

[명령행 옵션]
  --exam-id           : 시험 ID (필수). make_sheet.py나 웹에서 답안지를 만들 때 나온 값 (예: EX-20261012-ABC123)
  --make-key-template : 붙이면 빈 정답표 엑셀만 만들고 바로 끝냄 (다른 옵션은 무시)
  --key               : 정답표 파일 (.xlsx 또는 .json). 주면 이 시험의 정답표로 저장함.
                        생략하면 전에 저장해 둔 정답표(storage/exams/<시험ID>/answer_key.json)를 씀
  --photos            : 답안지 사진 폴더 또는 사진 파일 하나. 폴더면 IMG_EXT 확장자 파일만 이름순으로 처리
  --out               : 결과 엑셀 경로 (기본: storage/exams/<시험ID>/results.xlsx)

[결과 확인]
  터미널 출력
    빈 정답표: .../answer_key_template.xlsx            ← --make-key-template일 때
    정답표 저장: 20문항, 만점 24점                      ← --key를 줬을 때
    사진마다 한 줄
      student01.jpg: 학번 2026123456  {'ok': 18, 'ambiguous': 2}  ← 검수 필요
        → 학번 raw(못 읽은 자리는 '?'), 판독 상태별 문항 수
          (ok=정상 1개, multi=복수 응답, blank=무응답, ambiguous=애매해서 판정 보류)
        → 애매한 문항이나 학번 이상이 있으면 끝에 "← 검수 필요"
      student02.jpg: align_failed - 사유                ← 정렬 실패(재촬영 필요) / wrong_exam = 다른 시험 답안지
    채점 결과 엑셀: <경로>                              ← 최종 결과 파일
  저장 파일
    storage/exams/<시험ID>/answer_key.json             ← 정답표
    storage/exams/<시험ID>/sheets/S-XXXXXXXX.json       ← 사진 한 장의 판독 결과
    storage/exams/<시험ID>/sheets/S-XXXXXXXX_aligned.jpg ← 정렬된 이미지 (검수 화면용)
    결과 엑셀 (--out 또는 기본 경로)                    ← 학생별 점수표 등 시트 5개

[내부 처리 순서]
  1. 시험 기록(exam.json) 확인 → 없으면 오류 종료
  2. (--make-key-template) 빈 정답표 엑셀 만들고 종료
  3. (--key) 정답표 읽어서 저장
  4. (--photos) 사진마다 grading_service.process_photo() = P1 정렬 → P2 시험 확인 → P3 판독 → 저장
  5. grading_service.export_results() = 이 시험에 저장된 답안지 전체를 P4 채점 → P5 엑셀
     (이번에 넣은 사진만이 아니라 전에 저장된 답안지도 모두 포함)
     정답표가 하나도 저장돼 있지 않으면 여기서 ValueError로 멈춤

[다른 파일과의 관계]
  - app/grading_service.py : 실제 처리(저장 포함). 웹 API(app/main.py)도 같은 함수를 씀
  - app/service.py         : 시험 기록 읽기 (get_exam)
  - pipeline/flow.py       : grading_service가 내부에서 부르는 P1~P5 단계 함수
"""
import argparse
import glob
import os
import sys

import cv2

# backend 폴더를 import 경로에 추가 → backend 안의 app 패키지를 불러올 수 있게 함
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app import grading_service as gs  # noqa: E402
from app import service  # noqa: E402

# 사진으로 인정하는 파일 확장자 (소문자로 바꿔서 비교하므로 .JPG도 포함됨)
IMG_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff")


def main():
    """명령행 옵션을 읽어 정답표 저장 → 사진 판독·저장 → 결과 엑셀 생성까지 실행.

    옵션에 따라 일부 단계만 실행됨 (예: --make-key-template이면 빈 정답표만 만들고 끝).
    자세한 흐름은 모듈 설명 [내부 처리 순서] 참고.

    매개변수:
        없음 (명령행 옵션을 sys.argv에서 읽음)
    반환:
        None. 결과는 터미널 출력과 저장 파일로 나옴
    예외:
        SystemExit: 시험 ID가 없거나 사진을 하나도 못 찾았을 때 "[오류] ..." 메시지로 종료
        ValueError: 정답표 형식 오류·문항 수 불일치, 또는 저장된 정답표가 없는 상태에서 엑셀을 만들 때
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--exam-id", required=True)
    ap.add_argument("--make-key-template", action="store_true", help="빈 정답표 엑셀만 만들고 끝냄")
    ap.add_argument("--key", help="정답표 파일 (.xlsx 또는 .json). 생략하면 저장된 정답표 사용")
    ap.add_argument("--photos", help="답안지 사진 폴더 (또는 파일 하나)")
    ap.add_argument("--out", help="결과 엑셀 경로 (기본: storage/exams/<시험ID>/results.xlsx)")
    a = ap.parse_args()

    # 1. 시험 기록 확인 (exam: exam.json 내용 dict. 문항 수·선지 수·양식 이름 등)
    exam = service.get_exam(a.exam_id)
    if not exam:
        raise SystemExit(f"[오류] 시험 {a.exam_id}을(를) 찾을 수 없습니다 (storage/exams 확인)")

    # 2. 빈 정답표 엑셀만 만들고 끝내는 경우
    if a.make_key_template:
        print("빈 정답표:", gs.make_answer_key_template(a.exam_id))
        return
    # 3. 정답표 파일 → 이 시험의 정답표로 저장 (key: AnswerKey 객체)
    if a.key:
        key = gs.import_answer_key(a.exam_id, a.key)
        print(f"정답표 저장: {key.num_questions}문항, 만점 {key.total_points:g}점")
    # 4. 사진 판독 (P1 정렬 → P2 시험 확인 → P3 판독 → 저장)
    if a.photos:
        # paths: 처리할 사진 경로 목록. 파일 하나면 그것만, 폴더면 사진 확장자 파일을 이름순으로
        paths = ([a.photos] if os.path.isfile(a.photos) else
                 sorted(p for p in glob.glob(os.path.join(a.photos, "*")) if p.lower().endswith(IMG_EXT)))
        if not paths:
            raise SystemExit(f"[오류] {a.photos}에서 사진을 찾지 못했습니다")
        for p in paths:
            img = cv2.imread(p)   # BGR 이미지 배열. 깨진 파일이면 None
            if img is None:
                print(f"  {os.path.basename(p)}: 이미지를 읽을 수 없음")
                continue
            # rec: 답안지 한 장 기록 dict (status, read=판독 결과, align=정렬 요약 등)
            rec = gs.process_photo(a.exam_id, img, os.path.basename(p))
            if rec["status"] == "read":
                # 판독 성공 → 학번(raw)과 상태별 문항 수 출력, 검수 필요하면 표시
                r = rec["read"]
                flag = "  ← 검수 필요" if r["summary"]["needs_review"] else ""
                print(f"  {os.path.basename(p)}: 학번 {r['student_id']['raw']}  {r['summary']['status_counts']}{flag}")
            else:
                # align_failed(정렬 실패) 또는 wrong_exam(다른 시험 답안지) → 상태와 사유 출력
                print(f"  {os.path.basename(p)}: {rec['status']} - {rec.get('error')}")
    # 5. 저장된 답안지 전체 P4 채점 → P5 엑셀
    out = gs.export_results(a.exam_id, a.out)
    print("채점 결과 엑셀:", out)


if __name__ == "__main__":
    main()
