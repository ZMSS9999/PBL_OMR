# pyright: basic
"""시험별 권한 (소유자 · 참여자) — 누가 어떤 시험을 보고 고칠 수 있는지 정함.

[역할]
  - 시험마다 소유자(담당 선생님) 한 명과 참여자 여러 명을 storage/exams/<exam_id>/access.json 에 저장함
  - 요청한 사람이 그 시험에서 어떤 권한인지(level) 계산해서 main.py 의 권한 검사에 넘겨 줌
  - exam.json(시험 기록)은 건드리지 않음 → 답안지 생성·채점 코드와 분리

[시험별 역할] (kim 11/7) 같은 사용자가 A 시험에서는 시험 관리자, B 시험에서는 참가자일 수 있음
  시험 관리자 : 시험을 만든 사람(owner) + 공동 시험 관리자로 추가된 사람(members 값 "manager")
  시험 참가자 : members 값 "review"(검수 가능) 또는 "view"(보기만)

[권한 단계] (숫자가 클수록 더 많이 할 수 있음)
  0 none   : 못 봄 (목록에도 안 나옴)
  1 view   : 보기만    — 시험 정보·답안지 PDF·정답표·판독 결과·채점 결과·엑셀 보기
  2 review : 검수 가능 — 보기 + 사진 올리기 + 검수 수정
  3 owner  : 시험 관리자 — 검수 + 정답표 입력·수정 + 참여자·문항 범위 관리 + 시험 삭제
  4 admin  : 관리자    — 모든 시험에서 소유자 권한 + 소유자(담당) 바꾸기

[문항 범위 담당] (통합 시험: 여러 선생님이 문항을 나눠 냄, kim 11/7)
  sections = [{"name": "물리", "from": 1, "to": 10, "assignees": ["teacher02"]}, ...]
  - 담당자는 자기 범위 문항만 정답 입력·검수할 수 있음 (my_scope 참고)
  - 소유자·관리자는 범위와 상관없이 모든 문항
  - 범위를 맡지 않은 '검수 가능' 참여자(예: 조교)는 예전처럼 모든 문항 검수
  - 결과·엑셀에는 범위(과목)별 소계가 붙음 (grading_service.section_scores)

[저장 구조]
  storage/exams/<exam_id>/access.json
    {"owner": "teacher01", "members": {"teacher02": "review", "ta01": "view"},
     "sections": [{"name": "물리", "from": 1, "to": 10, "assignees": ["teacher02"]}],
     "updated_at": "2026-11-07T14:00:00+09:00"}
  - access.json 이 없는 시험(관리자 기능 이전에 만든 시험)은 소유자 없음 → 관리자만 보임.
    관리자 화면 '지난 시험 관리'에서 담당 선생님을 정해 주면 됨

[주요 함수]
  get_access()      시험의 소유자·참여자 읽기
  set_owner()       소유자 정하기 (시험을 만들 때, 관리자가 담당을 바꿀 때)
  set_members()     참여자 목록 통째로 바꾸기
  level_for()       사용자 한 명의 이 시험 권한 단계 (0~4)
  set_sections()    문항 범위(과목) 목록 통째로 바꾸기
  my_scope()        사용자 한 명이 정답 입력·검수할 수 있는 문항 번호
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from . import service

NONE, VIEW, REVIEW, OWNER, ADMIN = 0, 1, 2, 3, 4
# 참여자에게 줄 수 있는 권한. manager = 공동 시험 관리자 (만든 사람과 같은 권한)
MEMBER_LEVELS = {"manager": OWNER, "view": VIEW, "review": REVIEW}
LEVEL_NAME = {NONE: "none", VIEW: "view", REVIEW: "review", OWNER: "owner", ADMIN: "admin"}
LEVEL_LABEL = {"view": "참가자 · 보기만", "review": "참가자 · 검수 가능", "manager": "시험 관리자",
               "owner": "시험 관리자", "admin": "관리자"}


def _path(exam_id: str) -> str | None:
    """access.json 경로. 시험 ID 형식이 틀리면 None (경로 조작 방지)."""
    if not service.EXAM_ID_RE.match(exam_id or ""):
        return None
    return os.path.join(service.EXAM_DIR, exam_id, "access.json")


def get_access(exam_id: str) -> dict[str, Any]:
    """시험의 소유자·참여자. 파일이 없으면 {"owner": None, "members": {}}."""
    p = _path(exam_id)
    if p and os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("owner", None)
        data.setdefault("members", {})
        data.setdefault("sections", [])
        return data
    return {"owner": None, "members": {}, "sections": []}


def _save(exam_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """access.json 저장 (수정 시각 붙임). 시험 폴더가 없으면 LookupError."""
    p = _path(exam_id)
    if not p or not os.path.isdir(os.path.dirname(p)):
        raise LookupError("시험을 찾을 수 없습니다")
    data["updated_at"] = datetime.now(service.KST).isoformat(timespec="seconds")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def set_owner(exam_id: str, owner: str | None) -> dict[str, Any]:
    """소유자 정하기. 새 소유자가 참여자 목록에 있었으면 참여자에서는 뺌 (권한이 두 번 잡히지 않게)."""
    data = get_access(exam_id)
    data["owner"] = owner or None
    if owner:
        data["members"].pop(owner, None)
    return _save(exam_id, data)


def set_members(exam_id: str, members: dict[str, str]) -> dict[str, Any]:
    """참여자 목록 통째로 바꾸기.

    매개변수:
        members: {아이디: "view" 또는 "review"} (예: {"teacher02": "review"})
    예외:
        ValueError: 권한 값이 view/review 가 아님
    """
    data = get_access(exam_id)
    clean = {}
    for u, lv in (members or {}).items():
        if lv not in MEMBER_LEVELS:
            raise ValueError(f"권한은 manager(시험 관리자) / review(검수 가능) / view(보기만)만 가능합니다: {u}")
        if u != data["owner"]:
            clean[u.lower()] = lv
    data["members"] = clean
    # 참여자에서 빠진 사람은 문항 범위 담당에서도 뺌
    people = set(clean) | ({data["owner"]} if data["owner"] else set())
    for sec in data.get("sections", []):
        sec["assignees"] = [u for u in sec.get("assignees", []) if u in people]
    return _save(exam_id, data)


def set_sections(exam_id: str, sections: list[dict[str, Any]], num_questions: int) -> dict[str, Any]:
    """문항 범위(과목) 목록 통째로 바꾸기.

    매개변수:
        sections: [{"name": "물리", "from": 1, "to": 10, "assignees": ["teacher02"]}, ...]
        num_questions: 시험 문항 수 (범위 검사용)
    예외:
        ValueError: 이름 없음, 번호가 1~문항 수 밖, from > to, 범위끼리 겹침, 담당자가 소유자·참여자가 아님
    """
    data = get_access(exam_id)
    people = set(data["members"]) | ({data["owner"]} if data["owner"] else set())
    clean, used = [], {}
    for sec in sections or []:
        name = str(sec.get("name", "")).strip()[:20]
        try:
            a, b = int(sec.get("from")), int(sec.get("to"))
        except (TypeError, ValueError):
            raise ValueError("문항 범위는 숫자로 적어 주세요")
        if not name:
            raise ValueError("문항 범위마다 과목(이름)을 적어 주세요")
        if not 1 <= a <= b <= num_questions:
            raise ValueError(f"{name}: 문항 범위는 1~{num_questions} 안에서 시작 ≤ 끝이어야 합니다")
        for q in range(a, b + 1):
            if q in used:
                raise ValueError(f"{q}번이 '{used[q]}'과(와) '{name}'에 겹칩니다")
            used[q] = name
        who = []
        for u in sec.get("assignees", []) or []:
            u = str(u).lower()
            if u not in people:
                raise ValueError(f"{name}: 담당자 {u}는 이 시험의 소유자·참여자가 아닙니다")
            if u not in who:
                who.append(u)
        clean.append({"name": name, "from": a, "to": b, "assignees": who})
    data["sections"] = sorted(clean, key=lambda x: x["from"])
    return _save(exam_id, data)


def my_scope(user: dict[str, Any], exam_id: str, num_questions: int,
             acc: dict[str, Any] | None = None) -> dict[str, Any]:
    """사용자 한 명이 이 시험에서 정답 입력(key)·검수(review)할 수 있는 문항 번호.

    규칙:
        소유자·관리자                       → 둘 다 모든 문항
        문항 범위를 맡은 사람               → 둘 다 맡은 범위 문항만 (권한 단계와 상관없이)
        범위를 안 맡은 '검수 가능' 참여자   → 정답 입력 없음, 검수는 모든 문항
        범위를 안 맡은 '보기만' 참여자      → 둘 다 없음
    반환:
        {"key": [1, 2, ...], "review": [...], "sections": [내가 맡은 범위 이름, ...], "all": 모든 문항 가능 여부}
    """
    acc = acc or get_access(exam_id)
    lv = level_for(user, exam_id, acc)
    every = list(range(1, num_questions + 1))
    if lv >= OWNER:
        return {"key": every, "review": every, "sections": [], "all": True}
    mine = [s for s in acc.get("sections", []) if user["username"] in s.get("assignees", [])]
    if mine:
        qs = sorted({q for s in mine for q in range(s["from"], s["to"] + 1)})
        return {"key": qs, "review": qs, "sections": [s["name"] for s in mine], "all": False}
    return {"key": [], "review": every if lv >= REVIEW else [], "sections": [], "all": False}



def level_for(user: dict[str, Any], exam_id: str, acc: dict[str, Any] | None = None) -> int:
    """사용자 한 명이 이 시험에서 가진 권한 단계 (0~4).

    매개변수:
        user: accounts 의 공개용 계정 dict ("username", "role" 필요)
        acc: 이미 읽어 둔 get_access() 결과 (목록 화면에서 여러 번 읽지 않게). None이면 새로 읽음
    """
    if user.get("role") == "admin":
        return ADMIN
    acc = acc or get_access(exam_id)
    if acc.get("owner") == user["username"]:
        return OWNER
    return MEMBER_LEVELS.get(acc.get("members", {}).get(user["username"], ""), NONE)
