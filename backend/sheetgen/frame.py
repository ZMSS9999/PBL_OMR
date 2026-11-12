# pyright: basic
"""모든 답안지 공통 '프레임' 규격 (방향별 고정값).

[이 파일의 역할]
  답안지에서 문항 수·선지 수와 상관없이 항상 같은 자리에 있는 것들
  (ArUco 마커 4개, QR 코드, 용지 크기, 정렬 이미지 해상도)을 한곳에 정의함
  → 답안지를 만드는 쪽(sheetgen)과 사진을 읽는 쪽(pipeline)이 같은 숫자를 공유

[핵심 아이디어]
  - ArUco 마커 4개와 QR 위치는 문항 수·선지 수와 상관없이 방향(세로/가로)마다 항상 같은 자리
  - 팀 양식대로 세로·가로 모두 마커 ID 0~3 (TL 0, TR 1, BL 2, BR 3)
    → 방향은 ID가 아니라 마커 배치 모양(세로로 긴지 가로로 긴지)으로 앎 (pipeline/align.py)
  - 그래서 처리 순서가: 양식을 모르는 채로 먼저 정렬 → 정렬 이미지의 고정 위치에서 QR 읽기 → 양식 찾기

[파이프라인에서 위치]
  - 준비 단계(답안지 생성): layout.py가 frame_dict()로 layout.json의 "frame" 항목을 만들고,
    render_pdf.py가 그 좌표대로 마커·QR을 그림
  - P1 정렬(pipeline/align.py): marker_position()으로 마커 위치(TL 등) 확인,
    marker_corners_mm()로 마커가 가야 할 목표 위치 계산, canonical_size_px()로 정렬 이미지 크기 결정,
    ORIENTATIONS[...]["qr_bbox_mm"]에서 QR 잘라 읽기
  - P3 판독(pipeline/read.py): CANONICAL_DPI(기준 해상도)를 씀

[입력 → 출력]
  방향 문자열("portrait" / "landscape") → 용지 크기, 마커 좌표, 정렬 이미지 크기, frame dict

[다른 파일과의 관계]
  - 이 파일은 다른 프로젝트 파일을 import하지 않음 (가장 밑바닥 규격 파일)
  - 이 파일을 쓰는 곳: sheetgen/layout.py, sheetgen/render_pdf.py, pipeline/align.py, pipeline/read.py,
    tools/smoke_test.py, tools/demo_end_to_end.py (보통 `from sheetgen import frame as F`로 씀)

[좌표계] (프로젝트 전체 공통)
  - 원점: 용지 왼쪽 위 / 단위: mm / x는 오른쪽, y는 아래로 증가 (이미지 좌표와 같은 방향)
  - px가 필요하면 layout.py의 mm_to_px(), bbox_to_px()로 변환 (기준 200dpi)

[주요 상수·함수]
  ARUCO_DICT_NAME, MARKER_SIZE_MM, MARKER_INSET_MM, MARKER_IDS, CANONICAL_DPI, QR_PREFIX, QR_SIZE_MM : 고정 규격 값
  ORIENTATIONS             : 방향별 용지 크기·마커 ID·QR 위치 표
  page_size()              : 방향 → 용지 (가로, 세로) mm
  marker_corners_mm()      : 마커 한 개의 네 꼭짓점 mm 좌표
  canonical_size_px()      : 정렬 이미지 크기 (px)
  marker_position()        : 마커 ID → 위치(TL/TR/BR/BL)
  MARKER_SETS              : 사진에서 찾을 마커 세트 (팀 양식 6x6 / 실제 인쇄본 4x4)
  frame_dict()             : layout.json의 "frame" 항목 dict 만들기

[사용 예]
  from sheetgen import frame as F
  F.page_size("portrait")            # (210.0, 297.0)
  F.canonical_size_px("portrait")    # (1654, 2339)
  F.marker_position(3)               # "BR"

[이 값을 바꾸면]
  이미 인쇄한 답안지와 좌표가 어긋남 → layout.py의 TEMPLATE_VERSION을 반드시 올릴 것
"""

# 마커 규격은 팀 좌표 JSON(PBL 문서/json양식)과 합성 데이터셋에 맞춤 (2026-10-14)
ARUCO_DICT_NAME = "DICT_6X6_50"   # OpenCV ArUco 사전 이름. 6x6 비트 패턴, ID 0~49
MARKER_SIZE_MM = 18.249   # 마커 한 변 길이 (검은 테두리 포함, 단위 mm). json width_norm 0.061443 × 297mm
MARKER_INSET_MM = 3.967   # 왼쪽·위쪽 가장자리 → 마커까지 거리(mm). json left_norm 0.013357 × 297mm
MARKER_INSET_FAR_MM = 4.232   # 오른쪽·아래쪽 가장자리 → 마커까지 거리(mm). json에서 오른쪽 마커 left_norm
                              # 0.92431 × 297 = 274.52mm → 297 - 274.52 - 18.249. 팀 양식은 좌우가 살짝 비대칭
                          # 프린터 인쇄 여백(보통 3~5mm)에 잘릴 수 있으니 인쇄 미리보기로 확인할 것
MARKER_IDS = {"TL": 0, "TR": 1, "BR": 3, "BL": 2}   # 위치 → 마커 ID (세로·가로 공통, 팀 json과 같음)
CANONICAL_DPI = 200       # 정렬 후 기준 이미지 해상도(dpi = 1인치당 픽셀 수). A4 세로 = 1654 x 2339 px

QR_PREFIX = "CM1"         # QR 내용 맨 앞 표식 (CheckMate 형식 버전 1). 우리 QR인지 구분용
QR_SIZE_MM = 26.0         # QR 한 변 길이(mm). 둘레 여백(quiet zone) 포함. 팀 양식의 QR 네모 칸 안 가운데에 둠

# 방향별 고정값
#   page_size_mm : (가로, 세로) mm. 세로 A4 = 210 x 297, 가로 A4 = 297 x 210
#   marker_ids   : 위치 → 마커 ID (TL=왼쪽 위, TR=오른쪽 위, BR=오른쪽 아래, BL=왼쪽 아래). 두 방향 같음
#   qr_bbox_mm   : QR 영역 [x, y, w, h] (mm, x·y는 영역 왼쪽 위 꼭짓점)
ORIENTATIONS = {
    "portrait": {   # 세로 양식: QR은 왼쪽 위 이름 칸 아래 네모 칸(25, 52.79, 35x35) 안 가운데
        "page_size_mm": (210.0, 297.0),
        "marker_ids": MARKER_IDS,
        "qr_bbox_mm": (29.5, 57.29, QR_SIZE_MM, QR_SIZE_MM),
    },
    "landscape": {  # 가로 양식: QR은 왼쪽 패널 아래 "QR 코드" 네모 칸(32, 148.5, 30x30) 안 가운데
        "page_size_mm": (297.0, 210.0),
        "marker_ids": MARKER_IDS,
        "qr_bbox_mm": (34.0, 150.5, QR_SIZE_MM, QR_SIZE_MM),
    },
}

# 마커 세트: 사진에서 찾을 수 있는 마커 종류 (align.py가 둘 다 찾아 보고 더 잘 맞는 쪽을 씀)
#   "team"  : 팀 좌표 JSON·합성 데이터셋·우리 PDF 생성기 양식 (위 ARUCO_DICT_NAME, MARKER_IDS, 여백 값)
#   "print" : 실제로 인쇄해서 쓴 답안지 (PBL 문서/PDF 생성기 양식/OMR_A4_가로형_수정본_인쇄.pptx로 인쇄한 것)
#             - 마커 사전 DICT_4X4_50, ID 순서가 다름 (BR=2, BL=3)
#             - 마커 위치·크기는 pptx의 "ArUco" 자리 네모와 같음 (가로 16mm, 세로 18mm)
#             - 실측 사진 2장(20261014_203946.jpg, 20261008_120040.jpg)에서 코너 오차 1.7~1.9px로 확인 (가로만)
#             - 세로 인쇄본은 실측 사진이 없어 세로형_수정본.pptx 자리 네모 값을 그대로 씀 (미확인)
#   geometry: 방향 → (마커 한 변 mm, {위치: 마커 왼쪽 위 꼭짓점 (x, y) mm}). None이면 위의 팀 여백 값으로 계산
MARKER_SETS = {
    "team": {"dictionary": ARUCO_DICT_NAME, "marker_ids": MARKER_IDS, "geometry": None},
    "print": {
        "dictionary": "DICT_4X4_50",
        "marker_ids": {"TL": 0, "TR": 1, "BR": 2, "BL": 3},
        "geometry": {
            "landscape": (16.0, {"TL": (6.0, 5.0), "TR": (275.0, 5.0), "BR": (275.0, 189.0), "BL": (6.0, 189.0)}),
            "portrait": (18.0, {"TL": (6.0, 5.0), "TR": (186.0, 5.0), "BR": (186.0, 274.0), "BL": (6.0, 274.0)}),
        },
    },
}


def page_size(orientation: str) -> tuple[float, float]:
    """방향 → 용지 (가로, 세로) mm.

    매개변수:
        orientation: 용지 방향 ("portrait" 세로 / "landscape" 가로)
    반환:
        (가로 mm, 세로 mm) 튜플 (예: "portrait" → (210.0, 297.0))
    예외:
        KeyError: ORIENTATIONS에 없는 방향 문자열일 때
    """
    return ORIENTATIONS[orientation]["page_size_mm"]


def marker_corners_mm(orientation: str, pos: str, marker_set: str = "team") -> list[list[float]]:
    """마커 한 개의 네 꼭짓점 mm 좌표 (TL, TR, BR, BL 순서).

    cv2.aruco가 돌려주는 코너 순서와 같게 맞춰 둠 → align.py에서 순서 그대로 짝지으면 됨.
    P1 정렬에서 '사진 속 마커 꼭짓점'을 '이 좌표(정면 기준 위치)'로 옮기는 변환을 구할 때 씀.

    매개변수:
        orientation: 용지 방향 ("portrait" / "landscape")
        pos: 마커 위치 ("TL" 왼쪽 위 / "TR" 오른쪽 위 / "BR" 오른쪽 아래 / "BL" 왼쪽 아래)
        marker_set: 마커 세트 이름 ("team" 기본 / "print" 실제 인쇄본). MARKER_SETS 참고
    반환:
        [[x, y], ...] 4개 꼭짓점 mm 좌표 리스트
        (예: TL → [[3.967, 3.967], [22.216, 3.967], [22.216, 22.216], [3.967, 22.216]])
    예외:
        KeyError: 방향 또는 위치 문자열이 잘못됐을 때
    """
    geo = MARKER_SETS[marker_set]["geometry"]
    if geo is not None:                     # 인쇄본처럼 마커 자리를 직접 적어 둔 세트
        s, xy = geo[orientation]
        x, y = xy[pos]
        return [[x, y], [x + s, y], [x + s, y + s], [x, y + s]]
    W, H = page_size(orientation)          # 용지 가로·세로 (mm)
    s = MARKER_SIZE_MM                      # 마커 한 변 (mm)
    m, f = MARKER_INSET_MM, MARKER_INSET_FAR_MM   # 왼쪽·위쪽 여백, 오른쪽·아래쪽 여백 (mm)
    # 마커 왼쪽 위 꼭짓점 위치: 네 귀퉁이에서 여백만큼 안쪽
    x, y = {"TL": (m, m), "TR": (W - f - s, m),
            "BR": (W - f - s, H - f - s), "BL": (m, H - f - s)}[pos]
    return [[x, y], [x + s, y], [x + s, y + s], [x, y + s]]


def canonical_size_px(orientation: str, dpi: int = CANONICAL_DPI) -> tuple[int, int]:
    """정렬 이미지 크기 (가로 px, 세로 px). mm → inch(÷25.4) → px(×dpi).

    매개변수:
        orientation: 용지 방향 ("portrait" / "landscape")
        dpi: 해상도 (1인치당 픽셀 수, 기본 200)
    반환:
        (가로 px, 세로 px) 정수 튜플 (예: 세로 200dpi → (1654, 2339), 가로 → (2339, 1654))
    """
    W, H = page_size(orientation)
    return round(W / 25.4 * dpi), round(H / 25.4 * dpi)


def marker_position(marker_id: int, marker_set: str = "team") -> str | None:
    """마커 ID → 위치 ("TL" / "TR" / "BR" / "BL"). 우리 마커가 아니면 None.

    세로·가로 양식이 같은 ID를 쓰므로 ID로는 방향을 알 수 없음
    → 방향은 pipeline/align.py가 마커 배치 모양으로 판별함.

    매개변수:
        marker_id: 사진에서 검출한 ArUco 마커 ID (정수, 예: 0~3)
        marker_set: 마커 세트 이름 ("team" 기본 / "print"). 세트마다 ID 순서가 다름
    반환:
        위치 문자열 (예: team에서 3 → "BR", print에서 3 → "BL") 또는 그 세트에 없는 ID면 None
    """
    for pos, mid in MARKER_SETS[marker_set]["marker_ids"].items():
        if mid == marker_id:
            return pos
    return None


def frame_dict(orientation: str) -> dict:
    """layout.json의 "frame" 항목 내용. 다른 파트(웹·AI)가 마커·QR 위치를 알 수 있게 같이 저장.

    layout.py의 compute_layout()이 불러서 layout["frame"]에 넣음.

    매개변수:
        orientation: 용지 방향 ("portrait" / "landscape")
    반환:
        frame dict. 키 구성:
          orientation   : 방향 문자열
          page_size_mm  : [가로, 세로] mm
          unit, origin  : 좌표 단위("mm")와 원점("top-left") 설명
          canonical     : 정렬 이미지 기준 {"dpi", "width_px", "height_px"}
          aruco         : {"dictionary": 사전 이름, "marker_size_mm": 한 변 길이,
                           "markers": [{"id", "pos", "corners_mm"}, ...] (TL, TR, BR, BL 순서)}
          qr            : {"bbox_mm": [x, y, w, h], "format": QR 내용 형식 설명 문자열}
    예외:
        KeyError: 잘못된 방향 문자열일 때
    """
    cfg = ORIENTATIONS[orientation]                 # 이 방향의 고정값 표
    w_px, h_px = canonical_size_px(orientation)     # 정렬 이미지 가로·세로 px
    return {
        "orientation": orientation,
        "page_size_mm": list(cfg["page_size_mm"]),
        "unit": "mm",
        "origin": "top-left",
        "canonical": {"dpi": CANONICAL_DPI, "width_px": w_px, "height_px": h_px},
        "aruco": {
            "dictionary": ARUCO_DICT_NAME,
            "marker_size_mm": MARKER_SIZE_MM,
            "markers": [{"id": cfg["marker_ids"][p], "pos": p, "corners_mm": marker_corners_mm(orientation, p)}
                        for p in ("TL", "TR", "BR", "BL")],
        },
        "qr": {
            "bbox_mm": list(cfg["qr_bbox_mm"]),
            "format": f"{QR_PREFIX}|<exam_id>|<template_id>|<template_version>|<subject>|<exam_name>",
        },
    }
