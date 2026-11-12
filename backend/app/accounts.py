# pyright: basic
"""계정·로그인 (관리자 / 선생님) — 비밀번호 저장, 로그인 토큰 발급·확인, 계정 관리.

[역할]
  - 사용자 계정을 storage/users.json 한 파일에 저장함 (DB 없이 파일로 관리)
  - 비밀번호는 원문을 저장하지 않고 PBKDF2-SHA256 해시(솔트 포함)만 저장함
  - 로그인 성공 시 서명한 토큰을 만들어 줌 → main.py 가 브라우저 쿠키(cm_session)로 내려 줌
  - 관리자 화면의 계정 관리(만들기·이름/역할 바꾸기·비활성화·비밀번호 초기화) 함수도 여기 있음

[계정 역할(role) 두 가지] (kim 11/7: 관리자 화면과 사용자 화면을 나눔)
  admin : 관리자. 관리자 화면(지난 시험 관리·계정·양식·시스템 설정)만 씀. 모든 시험을 열어 볼 수 있음
  user  : 사용자. 시험을 만들 수 있고(→ 그 시험의 시험 관리자), 다른 시험에는 시험 참가자로 들어갈 수 있음
  시험 관리자 / 시험 참가자는 계정 역할이 아니라 시험마다 정해지는 역할임 (app/access.py)
  (예전 역할 이름 teacher·manager·participant 는 읽을 때 user 로 바꿈)

[토큰 형식]
  "<아이디>.<만료 시각(초)>.<토큰 버전>.<서명>"
  - 서명 = HMAC-SHA256(서버 비밀 키, 앞의 세 값). 비밀 키는 storage/secret.key (처음 실행 때 자동 생성)
  - 토큰 버전(token_ver)은 계정마다 있는 숫자 → 비밀번호 변경·초기화·비활성화 때 1 올림
    → 예전에 발급한 토큰이 모두 무효가 됨 (다른 브라우저의 로그인도 끊김)

[저장 구조]
  storage/users.json  : {"users": {"<아이디>": {계정 정보}, ...}}
  storage/secret.key  : 토큰 서명용 비밀 키 (32바이트 16진수). 지우면 모든 로그인이 끊김

[처음 실행]
  계정이 하나도 없으면 needs_setup() 이 True → 웹 로그인 화면이 '첫 관리자 계정 만들기'를 보여 줌
  → setup_first_admin() 은 계정이 0개일 때만 동작함 (이미 있으면 거절)

[주요 함수]
  needs_setup()          계정이 하나도 없는지
  setup_first_admin()    첫 관리자 계정 만들기
  authenticate()         아이디·비밀번호 확인 → 계정 정보
  issue_token()          로그인 토큰 만들기
  user_from_token()      토큰 확인 → 계정 정보 (틀리거나 만료면 None)
  list_users()           계정 목록 (비밀번호 해시는 빼고)
  create_user()          계정 만들기 (관리자)
  update_user()          이름·역할·사용 여부 바꾸기 (관리자)
  reset_password()       임시 비밀번호로 초기화 (관리자)
  change_password()      내 비밀번호 바꾸기 (본인)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime
from typing import Any

from . import service

# 계정 파일 경로
USERS_PATH = os.path.join(service.STORAGE_DIR, "users.json")
# 토큰 서명 키 파일 경로
SECRET_PATH = os.path.join(service.STORAGE_DIR, "secret.key")

ROLES = ("admin", "user")                       # 관리자 / 사용자
ROLE_LABEL = {"admin": "관리자", "user": "사용자"}
ROLE_ERROR = "역할은 admin(관리자) 또는 user(사용자)만 가능합니다"
USERNAME_RE = re.compile(r"^[a-z0-9_.-]{3,30}$")  # 아이디: 영문 소문자·숫자·_ . - 3~30자
MIN_PASSWORD = 8                                # 비밀번호 최소 길이
PBKDF2_ROUNDS = 200_000                         # 해시 반복 횟수 (클수록 느리고 안전)
TOKEN_HOURS = 12                                # 기본 로그인 유지 시간
TOKEN_DAYS_REMEMBER = 30                        # '로그인 상태 유지'를 고르면 유지 기간

# 같은 파일을 여러 요청이 동시에 고치지 않게 막는 잠금
_lock = threading.Lock()


class AccountError(ValueError):
    """계정 관련 입력 오류 (아이디 중복, 비밀번호 너무 짧음, 마지막 관리자 비활성화 등). main.py 가 422/409 로 바꿈."""


# ---------------- 파일 읽기·쓰기 ----------------

def _now() -> str:
    """지금 시각 (한국 시간 ISO 문자열, 초 단위)."""
    return datetime.now(service.KST).isoformat(timespec="seconds")


def _load() -> dict[str, Any]:
    """users.json 읽기. 없으면 빈 구조."""
    if not os.path.exists(USERS_PATH):
        return {"users": {}}
    with open(USERS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    for u in data.get("users", {}).values():
        if u.get("role") not in ROLES:          # 예전 역할 이름(teacher 등) → 사용자
            u["role"] = "user"
    return data


def _save(data: dict[str, Any]) -> None:
    """users.json 저장. 임시 파일에 다 쓴 뒤 바꿔 끼움 → 저장 도중 꺼져도 파일이 깨지지 않음."""
    os.makedirs(service.STORAGE_DIR, exist_ok=True)
    tmp = USERS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, USERS_PATH)


def _secret() -> bytes:
    """토큰 서명 키. 파일이 없으면 새로 만들어 저장함."""
    if not os.path.exists(SECRET_PATH):
        os.makedirs(service.STORAGE_DIR, exist_ok=True)
        with open(SECRET_PATH, "w", encoding="utf-8") as f:
            f.write(secrets.token_hex(32))
    with open(SECRET_PATH, encoding="utf-8") as f:
        return bytes.fromhex(f.read().strip())


# ---------------- 비밀번호 ----------------

def _hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """비밀번호 → (솔트, 해시). 솔트 = 계정마다 다른 무작위 값 → 같은 비밀번호여도 해시가 달라짐."""
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ROUNDS)
    return salt, digest.hex()


def _check_password_rule(password: str) -> None:
    """비밀번호 규칙 검사 (8자 이상). 어기면 AccountError."""
    if len(password or "") < MIN_PASSWORD:
        raise AccountError(f"비밀번호는 {MIN_PASSWORD}자 이상이어야 합니다")


def _public(u: dict[str, Any]) -> dict[str, Any]:
    """계정 정보에서 비밀번호 해시·솔트를 뺀 공개용 dict (API 응답용)."""
    return {k: u.get(k) for k in ("username", "name", "role", "active", "created_at", "last_login", "must_change")} \
        | {"role_label": ROLE_LABEL.get(u.get("role", ""), "")}


def _new_user(username: str, name: str, role: str, password: str) -> dict[str, Any]:
    """새 계정 dict 만들기 (입력 검사 포함)."""
    username = (username or "").strip().lower()
    if not USERNAME_RE.match(username):
        raise AccountError("아이디는 영문 소문자·숫자·_ . - 로 3~30자여야 합니다")
    if role not in ROLES:
        raise AccountError(ROLE_ERROR)
    name = (name or "").strip()[:30] or username
    _check_password_rule(password)
    salt, h = _hash_password(password)
    return {"username": username, "name": name, "role": role, "active": True,
            "salt": salt, "pw_hash": h, "token_ver": 1, "must_change": False,
            "created_at": _now(), "last_login": None}


# ---------------- 처음 실행 ----------------

def needs_setup() -> bool:
    """계정이 하나도 없으면 True (웹이 '첫 관리자 계정 만들기' 화면을 보여 줌)."""
    return not _load()["users"]


def setup_first_admin(username: str, name: str, password: str) -> dict[str, Any]:
    """첫 관리자 계정 만들기. 계정이 이미 하나라도 있으면 AccountError (누구나 관리자를 만들 수 없게).

    반환:
        공개용 계정 dict (예: {"username": "admin", "name": "김재형", "role": "admin", ...})
    """
    with _lock:
        data = _load()
        if data["users"]:
            raise AccountError("이미 계정이 있습니다. 관리자에게 계정을 요청하세요")
        u = _new_user(username, name, "admin", password)
        data["users"][u["username"]] = u
        _save(data)
    return _public(u)


# ---------------- 로그인 ----------------

def authenticate(username: str, password: str) -> dict[str, Any] | None:
    """아이디·비밀번호 확인. 맞고 사용 중인 계정이면 공개용 계정 dict, 아니면 None.

    성공하면 last_login(마지막 로그인 시각)을 기록함.
    """
    username = (username or "").strip().lower()
    with _lock:
        data = _load()
        u = data["users"].get(username)
        if not u or not u.get("active"):
            return None
        _, h = _hash_password(password or "", u["salt"])
        if not hmac.compare_digest(h, u["pw_hash"]):
            return None
        u["last_login"] = _now()
        _save(data)
    return _public(u)


def issue_token(username: str, remember: bool = False) -> tuple[str, int]:
    """로그인 토큰 만들기.

    매개변수:
        username: 아이디
        remember: True면 30일, False면 12시간 유지
    반환:
        (토큰 문자열, 유지 시간(초)) — 유지 시간은 쿠키 max_age 로 씀
    """
    u = _load()["users"][username]
    max_age = TOKEN_DAYS_REMEMBER * 86400 if remember else TOKEN_HOURS * 3600
    body = f"{username}.{int(time.time()) + max_age}.{u['token_ver']}"
    sig = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}", max_age


def user_from_token(token: str | None) -> dict[str, Any] | None:
    """토큰 확인 → 공개용 계정 dict. 서명이 틀리거나, 만료됐거나, 계정이 비활성·토큰 버전이 다르면 None."""
    if not token or token.count(".") < 3:
        return None
    body, sig = token.rsplit(".", 1)
    good = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, good):
        return None
    username, exp, ver = body.rsplit(".", 2)
    if not exp.isdigit() or int(exp) < time.time():
        return None
    u = _load()["users"].get(username)
    if not u or not u.get("active") or str(u.get("token_ver")) != ver:
        return None
    return _public(u)


# ---------------- 계정 관리 (관리자) ----------------

def list_users() -> list[dict[str, Any]]:
    """계정 목록 (공개용 dict, 관리자 먼저·아이디 순)."""
    users = _load()["users"].values()
    return sorted((_public(u) for u in users), key=lambda u: (u["role"] != "admin", u["username"]))


def get_user(username: str) -> dict[str, Any] | None:
    """계정 하나 (공개용 dict). 없으면 None."""
    u = _load()["users"].get((username or "").lower())
    return _public(u) if u else None


def create_user(username: str, name: str, role: str, password: str | None = None) -> tuple[dict[str, Any], str | None]:
    """계정 만들기 (관리자).

    매개변수:
        password: 비워 두면 임시 비밀번호를 만들어 줌 (첫 로그인 후 바꾸도록 must_change 표시)
    반환:
        (공개용 계정 dict, 만든 임시 비밀번호 또는 None)
    예외:
        AccountError: 아이디 형식 오류·중복, 비밀번호 규칙 위반
    """
    temp = None
    if not password:
        temp = _temp_password()
    with _lock:
        data = _load()
        u = _new_user(username, name, role, password or temp or "")
        if u["username"] in data["users"]:
            raise AccountError("이미 있는 아이디입니다")
        u["must_change"] = temp is not None
        data["users"][u["username"]] = u
        _save(data)
    return _public(u), temp


def _active_admins(data: dict[str, Any]) -> list[str]:
    """사용 중인 관리자 아이디 목록."""
    return [k for k, u in data["users"].items() if u.get("role") == "admin" and u.get("active")]


def update_user(username: str, *, name: str | None = None, role: str | None = None,
                active: bool | None = None, actor: str | None = None) -> dict[str, Any]:
    """계정 이름·역할·사용 여부 바꾸기 (관리자). 보낸 값만 바꿈.

    안전 장치:
        - 마지막 남은 관리자는 사용자로 바꾸거나 비활성화할 수 없음 (관리 화면에 아무도 못 들어오게 되는 것 방지)
        - 자기 자신은 비활성화할 수 없음
    예외:
        LookupError: 계정이 없음
        AccountError: 위 안전 장치에 걸림, 역할 값 오류
    """
    with _lock:
        data = _load()
        u = data["users"].get((username or "").lower())
        if not u:
            raise LookupError("계정을 찾을 수 없습니다")
        if role is not None and role not in ROLES:
            raise AccountError(ROLE_ERROR)
        losing_admin = u["role"] == "admin" and u.get("active") and ((role is not None and role != "admin") or active is False)
        if losing_admin and _active_admins(data) == [u["username"]]:
            raise AccountError("마지막 관리자 계정은 사용자로 바꾸거나 사용 중지할 수 없습니다")
        if active is False and actor == u["username"]:
            raise AccountError("자기 계정은 사용 중지할 수 없습니다")
        if name is not None:
            u["name"] = name.strip()[:30] or u["username"]
        if role is not None:
            u["role"] = role
        if active is not None and active != u.get("active"):
            u["active"] = active
            u["token_ver"] += 1        # 사용 중지하면 바로 로그아웃되게 토큰 무효화
        _save(data)
    return _public(u)


def _temp_password() -> str:
    """임시 비밀번호 (영문·숫자 10자, 헷갈리는 글자 0 O 1 l I 제외)."""
    abc = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(abc) for _ in range(10))


def reset_password(username: str) -> str:
    """임시 비밀번호로 초기화 (관리자). 그 계정의 기존 로그인은 모두 끊김.

    반환:
        새 임시 비밀번호 (관리자가 선생님에게 전달 → 로그인 후 바꾸도록 must_change 표시)
    """
    temp = _temp_password()
    with _lock:
        data = _load()
        u = data["users"].get((username or "").lower())
        if not u:
            raise LookupError("계정을 찾을 수 없습니다")
        u["salt"], u["pw_hash"] = _hash_password(temp)
        u["token_ver"] += 1
        u["must_change"] = True
        _save(data)
    return temp


def change_password(username: str, old: str, new: str) -> None:
    """내 비밀번호 바꾸기 (본인). 지금 비밀번호가 맞아야 함. 바꾸면 다른 브라우저의 로그인도 끊김.

    예외:
        AccountError: 지금 비밀번호가 틀림, 새 비밀번호 규칙 위반
    """
    _check_password_rule(new)
    with _lock:
        data = _load()
        u = data["users"].get(username)
        if not u:
            raise LookupError("계정을 찾을 수 없습니다")
        _, h = _hash_password(old or "", u["salt"])
        if not hmac.compare_digest(h, u["pw_hash"]):
            raise AccountError("지금 비밀번호가 맞지 않습니다")
        u["salt"], u["pw_hash"] = _hash_password(new)
        u["token_ver"] += 1
        u["must_change"] = False
        _save(data)
