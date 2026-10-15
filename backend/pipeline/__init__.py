# pyright: basic
"""pipeline 패키지: 답안지 사진 한 장을 판독 결과로 바꾸는 처리 단계 모음.

[구성]
  - align.py : P1 정렬. 사진 → 반듯하게 편 200dpi 정면 이미지(warped) + QR 정보
  - read.py  : P3 판독. 정면 이미지 + 양식 좌표 → 문항별 답·학번 (규칙 기반)
  - flow.py  : P1~P5 단계 연결부. P2 시험 확인도 여기서 처리하고,
               P4 채점(grading/score.py)·P5 엑셀(grading/excel.py)까지 이어 줌

[사용 예]
  from pipeline import flow
  results, xlsx = flow.run_batch(photos, exam, layout, key, "결과.xlsx")

이 파일 자체에는 코드 없음 (폴더를 파이썬 패키지로 인식시키는 용도).
"""
