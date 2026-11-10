# pyright: basic
"""관리자 화면용 기능 — 전체 시험 현황, 시험 목록 엑셀, 답안지 양식 목록, 시스템 설정(판독 기준값).

[역할]
  - 지난 시험 관리 : 모든 시험의 담당·날짜·답안지 수·상태(준비 / 검수 중 / 완료)를 한 번에 모아 줌
  - 목록 엑셀      : 위 표를 엑셀 파일로 만들어 줌 (관리자 화면 '엑셀로 내보내기')
  - 답안지 양식    : storage/templates 의 양식 좌표 파일 목록 + 양식마다 쓰는 시험 수
  - 시스템 설정    : 판독 기준값(ReadConfig 일부)을 storage/settings.json 에 저장
                     → 사진을 올릴 때 read_config() 로 읽어 판독(P3)에 넘김

[시험 상태 정하는 규칙]
  준비    : 올린 답안지가 0장
  검수 중 : 검수 대기 답안지가 1장 이상 (정답표가 있으면 채점 결과의 '검수 대기', 없으면 판독의 검수 필요 표시)
            또는 재촬영 필요 답안지가 1장 이상
  완료    : 위 둘 다 0장

[저장 구조]
  storage/settings.json : {"read": {"expo_retake": 0.24, ...}, "updated_at": ..., "updated_by": ...}
  (파일이 없거나 항목이 빠져 있으면 pipeline/read.py 의 ReadConfig 기본값을 씀)

[주요 함수]
  exam_overview()     시험 한 개의 현황 dict
  all_exam_overview() 전체 시험 현황 목록
  export_exam_list()  현황 목록 → 엑셀 파일
  list_templates()    양식 목록
  get_settings()      시스템 설정 (기본값·범위 포함)
  update_settings()   시스템 설정 저장
  read_config()       저장된 설정으로 만든 ReadConfig (판독에 넘김)
"""
from __future__ import annotations

import glob
import json
import os
from dataclasses import fields
from datetime import datetime
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from pipeline.read import ReadConfig
from . import access, accounts, service
from . import grading_service as gs

SETTINGS_PATH = os.path.join(service.STORAGE_DIR, "settings.json")

# 관리자 화면에서 바꿀 수 있는 판독 기준값: 이름 → (설명, 최소, 최대) 또는 (설명, 고를 수 있는 값 목록)
SETTING_SPECS: dict[str, dict[str, Any]] = {
    "expo_retake": {"label": "재촬영 기준 (인쇄 대비)", "min": 0.05, "max": 0.60,
                    "help": "인쇄 잉크 대비가 이 값보다 낮으면 '재촬영 필요(너무 밝음)'. 낮출수록 재촬영이 줄어듦 (임시값 0.24)"},
    "expo_min": {"label": "밝은 사진 기준 (인쇄 대비)", "min": 0.20, "max": 0.80,
                 "help": "이 값보다 낮으면 '조금 밝음' → 무응답 문항만 검수로 돌림"},
    "solid_min": {"label": "마킹 인정 농도", "min": 0.30, "max": 0.95,
                  "help": "칸 안쪽 잉크 농도가 이 값 이상이면 마킹으로 읽음"},
    "light_min": {"label": "흐린 자국 농도", "min": 0.03, "max": 0.30,
                  "help": "이 값 이상이면 흐린 마킹·지운 흔적으로 봄 (빈칸은 대부분 0.09 이하)"},
    "ring_min": {"label": "동그라미 표시 기준", "min": 0.05, "max": 0.50,
                 "help": "칸 바깥 링에 잉크가 이만큼 있으면 동그라미 표시로 봄"},
    "faint_policy": {"label": "흐린 마킹 처리", "choices": {"accept": "답으로 인정", "review": "검수로 돌림"},
                     "help": "흐린 마킹이 한 칸만 있을 때"},
    "expo_policy": {"label": "밝은 사진 처리", "choices": {"retake": "아주 밝으면 재촬영", "review": "재촬영 없이 검수"},
                    "help": "과노출 사진을 어떻게 처리할지"},
}


# ---------------- 지난 시험 관리 ----------------

def _now() -> str:
    return datetime.now(service.KST).isoformat(timespec="seconds")


def exam_overview(rec: dict[str, Any], names: dict[str, str] | None = None) -> dict[str, Any]:
    """시험 기록 하나 → 관리자 목록 한 줄 (담당·답안지 수·검수 대기·재촬영·상태·참여자).

    매개변수:
        rec: 시험 기록 dict (exam.json 내용)
        names: {아이디: 이름} (담당·참여자 이름 표시용). None이면 계정 파일에서 읽음
    """
    if names is None:
        names = {u["username"]: u["name"] for u in accounts.list_users()}
    exam_id = rec["exam_id"]
    acc = access.get_access(exam_id)
    try:
        sheets = gs.list_sheets(exam_id)
    except Exception:
        sheets = []
    n_retake = sum(1 for s in sheets if s.get("status") != "read")
    key = gs.get_answer_key(exam_id)
    n_pending = 0
    if key is not None and sheets:
        try:
            n_pending = sum(1 for g in gs.grade_sheets(exam_id, key) if g.get("status") == "검수 대기")
        except Exception:
            n_pending = 0
    else:
        # 정답표가 없으면 판독 결과의 '검수 필요' 표시 중 아직 검수하지 않은 장만 셈
        n_pending = sum(1 for s in sheets if s.get("status") == "read"
                        and s["read"]["summary"].get("needs_review") and not s["review"].get("updated_at"))
    if not sheets:
        status = "준비"
    elif n_pending or n_retake:
        status = "검수 중"
    else:
        status = "완료"
    owner = acc.get("owner")
    return {
        "exam_id": exam_id, "exam_name": rec.get("exam_name", ""), "subject": rec.get("subject", ""),
        "created_at": rec.get("created_at", ""), "num_questions": rec.get("num_questions"),
        "num_choices": rec.get("num_choices"), "orientation": rec.get("orientation"),
        "template_id": rec.get("template_id"), "template_version": rec.get("template_version"),
        "owner": owner, "owner_name": names.get(owner, owner) if owner else None,
        "members": [{"username": u, "name": names.get(u, u), "level": lv} for u, lv in acc["members"].items()],
        "has_key": key is not None, "n_sheets": len(sheets), "n_pending": n_pending, "n_retake": n_retake,
        "status": status,
    }


def all_exam_overview() -> list[dict[str, Any]]:
    """전체 시험 현황 (만든 시각 최근 순)."""
    names = {u["username"]: u["name"] for u in accounts.list_users()}
    return [exam_overview(r, names) for r in service.list_exams()]


def export_exam_list(path: str) -> str:
    """전체 시험 현황 → 엑셀 파일 (시트 1개 '시험 목록'). 만든 파일 경로를 돌려줌."""
    wb = Workbook()
    ws = wb.active
    ws.title = "시험 목록"
    head = ["시험명", "과목", "시험 ID", "담당", "참여자", "만든 날짜", "문항", "답안지(장)", "검수 대기", "재촬영", "상태"]
    ws.append(head)
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="E8F0FE")
    for e in all_exam_overview():
        members = ", ".join(f"{m['name']}({access.LEVEL_LABEL[m['level']]})" for m in e["members"])
        ws.append([e["exam_name"], e["subject"], e["exam_id"], e["owner_name"] or "(없음)", members,
                   (e["created_at"] or "")[:10], f"{e['num_questions']}문항 {e['num_choices']}지선다",
                   e["n_sheets"], e["n_pending"], e["n_retake"], e["status"]])
    for col, w in zip("ABCDEFGHIJK", (24, 14, 22, 12, 30, 12, 16, 11, 10, 9, 9)):
        ws.column_dimensions[col].width = w
    wb.save(path)
    return path


# ---------------- 답안지 양식 ----------------

def list_templates() -> list[dict[str, Any]]:
    """저장된 양식 좌표 목록. 양식 ID 예: P45Q5C = 세로(P)·45문항·5지선다 / L20Q4C = 가로(L)·20문항·4지선다.

    반환:
        [{"template_id": "P45Q5C", "version": "1", "orientation": "portrait", "num_questions": 45,
          "num_choices": 5, "n_exams": 3, "url": "/api/templates/P45Q5C/1"}, ...]
    """
    usage: dict[tuple[str, str], int] = {}
    for r in service.list_exams():
        k = (r.get("template_id", ""), str(r.get("template_version", "")))
        usage[k] = usage.get(k, 0) + 1
    out = []
    for p in sorted(glob.glob(os.path.join(service.TEMPLATE_DIR, "*.layout.json"))):
        try:
            with open(p, encoding="utf-8") as f:
                lay = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        tid, ver = lay.get("template_id", ""), str(lay.get("template_version", ""))
        spec = lay.get("spec", {}) if isinstance(lay.get("spec"), dict) else {}
        out.append({
            "template_id": tid, "version": ver,
            "orientation": spec.get("orientation") or lay.get("orientation") or ("landscape" if tid.startswith("L") else "portrait"),
            "num_questions": spec.get("num_questions") or lay.get("num_questions"),
            "num_choices": spec.get("num_choices") or lay.get("num_choices"),
            "n_exams": usage.get((tid, ver), 0),
            "modified_at": datetime.fromtimestamp(os.path.getmtime(p), service.KST).isoformat(timespec="seconds"),
            "url": f"/api/templates/{tid}/{ver}",
        })
    return out


# ---------------- 시스템 설정 ----------------

def _defaults() -> dict[str, Any]:
    """ReadConfig 기본값 중 관리자 화면에서 바꿀 수 있는 항목만."""
    base = ReadConfig()
    return {k: getattr(base, k) for k in SETTING_SPECS}


def _load() -> dict[str, Any]:
    if not os.path.exists(SETTINGS_PATH):
        return {"read": {}}
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"read": {}}
    data.setdefault("read", {})
    return data


def get_settings() -> dict[str, Any]:
    """시스템 설정 (지금 값 + 기본값 + 항목 설명·범위). 관리자 화면 '시스템 설정'이 그대로 씀."""
    data = _load()
    defaults = _defaults()
    values = {k: data["read"].get(k, v) for k, v in defaults.items()}
    return {"read": values, "defaults": defaults, "specs": SETTING_SPECS,
            "updated_at": data.get("updated_at"), "updated_by": data.get("updated_by")}


def update_settings(values: dict[str, Any], actor: str) -> dict[str, Any]:
    """판독 기준값 저장. 보낸 항목만 바꿈. 기본값과 같은 값은 파일에서 빼서 '기본값 따름'으로 둠.

    예외:
        ValueError: 모르는 항목, 범위 밖 숫자, 고를 수 없는 값, expo_retake > expo_min
    """
    data = _load()
    cur = {**_defaults(), **data["read"]}
    for k, v in (values or {}).items():
        spec = SETTING_SPECS.get(k)
        if spec is None:
            raise ValueError(f"바꿀 수 없는 설정입니다: {k}")
        if "choices" in spec:
            if v not in spec["choices"]:
                raise ValueError(f"{spec['label']}: 고를 수 있는 값이 아닙니다")
        else:
            try:
                v = round(float(v), 3)
            except (TypeError, ValueError):
                raise ValueError(f"{spec['label']}: 숫자를 넣어 주세요")
            if not spec["min"] <= v <= spec["max"]:
                raise ValueError(f"{spec['label']}: {spec['min']}~{spec['max']} 사이여야 합니다")
        cur[k] = v
    if cur["expo_retake"] > cur["expo_min"]:
        raise ValueError("재촬영 기준은 밝은 사진 기준보다 클 수 없습니다")
    defaults = _defaults()
    data["read"] = {k: v for k, v in cur.items() if k in SETTING_SPECS and v != defaults[k]}
    data["updated_at"] = _now()
    data["updated_by"] = actor
    os.makedirs(service.STORAGE_DIR, exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return get_settings()


def read_config() -> ReadConfig:
    """저장된 설정으로 만든 판독 설정값 (사진 올리기 API가 process_photo 에 넘김)."""
    names = {f.name for f in fields(ReadConfig)}
    over = {k: v for k, v in _load()["read"].items() if k in names and k in SETTING_SPECS}
    return ReadConfig(**over)
