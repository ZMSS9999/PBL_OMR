# pyright: basic
"""app 패키지 — CheckMate 웹 서버(FastAPI)와 서비스 층.

[들어 있는 파일]
  main.py            : 웹 API 입구. 요청을 받아 아래 두 파일의 함수를 부르고 응답 형식을 맞춤
  service.py         : 파이프라인 준비 단계. 답안지 PDF 생성, 시험 기록·양식 좌표 저장/읽기
  grading_service.py : 정답표 저장 → 사진 판독(P1 정렬·P2 시험 확인·P3 판독) → 검수 → P4 채점 → P5 엑셀,
                       단계 사이 결과를 storage/ 폴더에 저장

[실행] (backend 폴더에서)
    uvicorn app.main:app --reload --port 8000

[명령행 도구에서 쓰는 예]
    from app import service, grading_service
"""
