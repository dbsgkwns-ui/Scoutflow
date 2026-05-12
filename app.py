from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import traceback
import hashlib
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template, send_file, request

APP = Flask(__name__, template_folder=".")
APP_VERSION = "V59_K12_PUBLIC_DOMAIN_READY"
APP_PORT = 8058
BIND_HOST = os.environ.get("SCOUTFLOW_BIND_HOST", "0.0.0.0")

BASE = Path(__file__).resolve().parent
DATA_DIR = BASE / "data"
LOG_DIR = BASE / "logs"
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

CURRENT = DATA_DIR / "current_index.json"
SEED = DATA_DIR / "seed_index.json"
RUN_LOG = LOG_DIR / "last_server_run.log"

ADMIN_CONFIG = DATA_DIR / "admin_config.json"
DEFAULT_ADMIN_PASSWORD = "scoutflow2026"
ADMIN_TOKENS: set[str] = set()


def _password_hash(password: str, salt: str) -> str:
    return hashlib.sha256((salt + "::" + password).encode("utf-8")).hexdigest()


def _load_admin_config() -> Dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not ADMIN_CONFIG.exists():
        salt = secrets.token_hex(16)
        ADMIN_CONFIG.write_text(json.dumps({
            "password_hash": _password_hash(DEFAULT_ADMIN_PASSWORD, salt),
            "salt": salt,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        return json.loads(ADMIN_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        salt = secrets.token_hex(16)
        cfg = {
            "password_hash": _password_hash(DEFAULT_ADMIN_PASSWORD, salt),
            "salt": salt,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "recovered": True,
        }
        ADMIN_CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        return cfg


def _verify_admin_password(password: str) -> bool:
    cfg = _load_admin_config()
    salt = str(cfg.get("salt", ""))
    expected = str(cfg.get("password_hash", ""))
    return bool(password) and secrets.compare_digest(_password_hash(password, salt), expected)


def _set_admin_password(new_password: str) -> None:
    salt = secrets.token_hex(16)
    cfg = {
        "password_hash": _password_hash(new_password, salt),
        "salt": salt,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    ADMIN_CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def _admin_token_ok() -> bool:
    token = request.headers.get("X-Admin-Token", "").strip()
    return bool(token and token in ADMIN_TOKENS)


def _admin_required_response():
    return jsonify({"ok": False, "message": "관리자 로그인이 필요합니다."}), 403

BAD_LABELS = {"수집중", "검증필요", "공식확인 대기", "공격적인 대기", "확인중", "자동반영 대기"}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (ScoutFlow/58.0; Windows NT 10.0; Win64; x64)",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
}

state: Dict[str, Any] = {
    "running": False,
    "phase": "대기",
    "message": "서버 준비 완료",
    "checked": 0,
    "fixed": 0,
    "errors": 0,
    "last_success": "",
    "log": [],
}

def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def log(message: str, phase: str | None = None) -> None:
    if phase:
        state["phase"] = phase
    state["message"] = message
    line = f"{now()} · {state.get('phase','확인')} · {message}"
    state.setdefault("log", []).append(line)
    state["log"] = state["log"][-120:]
    RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
    with RUN_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")

def clean(x: Any) -> str:
    return re.sub(r"\s+", " ", str(x or "").replace("\xa0", " ")).strip()

def valid_birth(value: Any) -> bool:
    m = re.search(r"(19\d{2}|20\d{2})", str(value or ""))
    return bool(m and 1980 <= int(m.group(1)) <= 2011)

def valid_height(value: Any) -> bool:
    try:
        h = int(float(value))
    except Exception:
        return False
    return 145 <= h <= 215

def display_position_value(p: Dict[str, Any]) -> str:
    pos = clean(p.get("position"))
    if pos in BAD_LABELS:
        pos = ""
    if not pos:
        role = clean(p.get("position_role"))
        if role and role not in {"기타", "UNK"} and role not in BAD_LABELS:
            pos = role
    return pos

def complete_profile(p: Dict[str, Any]) -> bool:
    return bool(display_position_value(p) and valid_height(p.get("height_cm")) and valid_birth(p.get("birthdate")))

def clean_bad_profile_fields(p: Dict[str, Any]) -> bool:
    changed = False
    if clean(p.get("position")) in BAD_LABELS:
        p["position"] = ""
        changed = True
    if p.get("height_cm") and not valid_height(p.get("height_cm")):
        p["height_cm"] = None
        changed = True
    if p.get("birthdate") and not valid_birth(p.get("birthdate")):
        p["birthdate"] = ""
        changed = True
    return changed

def counts(players: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(players)
    complete = sum(1 for p in players if complete_profile(p))
    return {
        "total": total,
        "u": sum(1 for p in players if p.get("competition") == "U리그"),
        "k1": sum(1 for p in players if p.get("competition") == "K리그1"),
        "k2": sum(1 for p in players if p.get("competition") == "K리그2"),
        "k3": sum(1 for p in players if p.get("competition") == "K리그3"),
        "complete": complete,
        "complete_rate": round(complete / total * 100, 1) if total else 0.0,
    }

def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))

def safe_data() -> Dict[str, Any]:
    try:
        data = read_json(CURRENT)
        if not isinstance(data.get("players"), list) or len(data.get("players", [])) < 1:
            raise ValueError("current_index.json has no players")
        changed = apply_embedded_kleague_cache(data)
        if changed:
            save_data(data, touch_update_status=False)
        return data
    except Exception as e:
        log(f"current_index 손상 감지, seed로 복구: {e}", "자동복구")
        shutil.copy(SEED, CURRENT)
        data = read_json(CURRENT)
        changed = apply_embedded_kleague_cache(data)
        save_data(data, touch_update_status=True)
        return data


KLEAGUE_PROFILE_CACHE = DATA_DIR / "kleague_profile_cache.json"
POS_KO = {"FW":"공격수", "MF":"미드필더", "DF":"수비수", "GK":"골키퍼"}

def apply_embedded_kleague_cache(data: Dict[str, Any]) -> int:
    """Apply built-in K1/K2 profile cache without touching U-League rows."""
    try:
        cache_rows = json.loads(KLEAGUE_PROFILE_CACHE.read_text(encoding="utf-8")) if KLEAGUE_PROFILE_CACHE.exists() else []
    except Exception:
        cache_rows = []
    cache = {}
    for r in cache_rows:
        cache[(clean(r.get("competition")), clean(r.get("name")), clean(r.get("team")))] = r
    changed = 0
    for p in data.get("players", []):
        if p.get("competition") not in {"K리그1", "K리그2"}:
            continue
        r = cache.get((clean(p.get("competition")), clean(p.get("name")), clean(p.get("team"))))
        if not r:
            continue
        before = (p.get("position"), p.get("height_cm"), p.get("birthdate"))
        p["position"] = r.get("position")
        p["position_group"] = r.get("position")
        p["position_role"] = POS_KO.get(r.get("position"), r.get("position") or "")
        p["height_cm"] = r.get("height_cm")
        p["birthdate"] = r.get("birthdate")
        p["display_position"] = p.get("position_role") or POS_KO.get(r.get("position"), r.get("position") or "")
        try:
            yy = re.search(r"(19\d{2}|20\d{2})", str(p.get("birthdate"))).group(1)[2:]
            p["display_profile"] = f"{int(float(p.get('height_cm')))}cm / {yy}년생"
        except Exception:
            p["display_profile"] = ""
        p["profile_verified"] = True
        p["profile_source"] = "embedded K리그1/2 profile cache hard display fix"
        p["profile_source_url"] = "https://www.kleague.com/player.do?type=active"
        after = (p.get("position"), p.get("height_cm"), p.get("birthdate"), p.get("display_position"), p.get("display_profile"))
        if before != after:
            changed += 1
    data.setdefault("metadata", {})["k12_profile_cache_applied"] = True
    return changed


def save_data(data: Dict[str, Any], touch_update_status: bool = True) -> None:
    apply_embedded_kleague_cache(data)
    for p in data.get("players", []):
        clean_bad_profile_fields(p)
    metadata = data.setdefault("metadata", {})
    previous_update_status = metadata.get("update_status") if isinstance(metadata.get("update_status"), dict) else {}
    metadata["version"] = APP_VERSION
    metadata["counts"] = counts(data.get("players", []))
    if touch_update_status or not previous_update_status:
        metadata["update_status"] = {
            "last_success_at_display": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "message": state.get("message", ""),
        }
    else:
        metadata["update_status"] = previous_update_status
    CURRENT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def http_get(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding
    return r.text

def parse_kusf_detail(url: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not url or "kusf.or.kr" not in str(url):
        return out
    html = http_get(url)
    soup = BeautifulSoup(html, "html.parser")
    text = clean(soup.get_text(" "))

    for tr in soup.find_all("tr"):
        cells = [clean(c.get_text(" ")) for c in tr.find_all(["th", "td"])]
        cells = [c for c in cells if c]
        for i in range(len(cells) - 1):
            key = re.sub(r"\s+", "", cells[i])
            val = cells[i + 1]
            if key == "포지션" and val:
                out["position"] = val
            elif key in {"신장", "키"}:
                m = re.search(r"(\d{2,3})", val)
                if m and valid_height(m.group(1)):
                    out["height_cm"] = int(m.group(1))
            elif key == "생년월일":
                m = re.search(r"(19\d{2}|20\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일", val)
                if m:
                    bd = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
                    if valid_birth(bd):
                        out["birthdate"] = bd
            elif key == "출신고":
                out["high_school"] = val
            elif key == "등번호":
                m = re.search(r"\d+", val)
                if m:
                    out["jersey"] = m.group(0)

    if not out.get("height_cm"):
        m = re.search(r"(?:신장|키)\s*(\d{2,3})\s*cm", text)
        if m and valid_height(m.group(1)):
            out["height_cm"] = int(m.group(1))

    if not out.get("birthdate"):
        m = re.search(r"생년월일\s*(19\d{2}|20\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일", text)
        if m:
            bd = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            if valid_birth(bd):
                out["birthdate"] = bd

    if not out.get("position"):
        m = re.search(r"포지션\s*(골키퍼|수비수|미드필더|공격수|GK|DF|MF|FW)", text)
        if m:
            out["position"] = m.group(1)

    return out

MONTHS = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
    "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}

def parse_transfermarkt_detail(url: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not url or "transfermarkt" not in str(url):
        return out
    html = http_get(url)
    soup = BeautifulSoup(html, "html.parser")
    text = clean(soup.get_text(" "))

    # Height: 1,82 m / 1.82 m
    m = re.search(r"\b([12])[,\.](\d{2})\s*m\b", text)
    if m:
        h = int(m.group(1)) * 100 + int(m.group(2))
        if valid_height(h):
            out["height_cm"] = h

    # Birthdate: Feb 22, 1997 / 22/02/1997 / 1997년 2월 22일
    m = re.search(r"\b([A-Z][a-z]{2})\s+(\d{1,2}),\s+(19\d{2}|20\d{2})\b", text)
    if m:
        mo = MONTHS.get(m.group(1).lower())
        if mo:
            bd = f"{m.group(3)}-{mo}-{int(m.group(2)):02d}"
            if valid_birth(bd):
                out["birthdate"] = bd
    if not out.get("birthdate"):
        m = re.search(r"\b(\d{2})/(\d{2})/(19\d{2}|20\d{2})\b", text)
        if m:
            bd = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
            if valid_birth(bd):
                out["birthdate"] = bd
    if not out.get("birthdate"):
        m = re.search(r"(19\d{2}|20\d{2})년\s*(\d{1,2})월\s*(\d{1,2})일", text)
        if m:
            bd = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            if valid_birth(bd):
                out["birthdate"] = bd

    pos_map = [
        ("Goalkeeper", "GK"), ("Centre-Back", "DF"), ("Left-Back", "DF"), ("Right-Back", "DF"), ("Defender", "DF"),
        ("Defensive Midfield", "MF"), ("Central Midfield", "MF"), ("Attacking Midfield", "MF"), ("Midfielder", "MF"),
        ("Centre-Forward", "FW"), ("Second Striker", "FW"), ("Left Winger", "FW"), ("Right Winger", "FW"), ("Forward", "FW"),
        ("골키퍼", "GK"), ("수비수", "DF"), ("미드필더", "MF"), ("공격수", "FW"),
    ]
    for label, code in pos_map:
        if label in text:
            out["position"] = code
            break

    return out

def parse_kleague_detail(url: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not url or "kleague.com" not in str(url):
        return out
    html = http_get(url)
    soup = BeautifulSoup(html, "html.parser")
    text = clean(soup.get_text(" "))

    m = re.search(r"Position\s*(GK|DF|MF|FW)", text, re.I)
    if m:
        out["position"] = m.group(1).upper()
    m = re.search(r"Height\s*(\d{2,3})", text, re.I)
    if m and valid_height(m.group(1)):
        out["height_cm"] = int(m.group(1))
    m = re.search(r"Birth Date\s*(19\d{2}|20\d{2})[/\-\.](\d{1,2})[/\-\.](\d{1,2})", text, re.I)
    if m:
        bd = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        if valid_birth(bd):
            out["birthdate"] = bd

    return out

def enrich_player(p: Dict[str, Any]) -> Dict[str, Any]:
    if complete_profile(p):
        return p

    sources = []
    if p.get("competition") == "U리그":
        sources.append(parse_kusf_detail)
    # For K League/K3, try detail_url if available.
    sources.extend([parse_kleague_detail, parse_transfermarkt_detail])

    for parser in sources:
        try:
            d = parser(p.get("detail_url", ""))
            for k, v in d.items():
                if v not in [None, ""]:
                    p[k] = v
            clean_bad_profile_fields(p)
            if complete_profile(p):
                return p
        except Exception as e:
            log(f"{p.get('name')} 프로필 확인 실패: {e}", "프로필확인")
    return p

def repair_worker() -> None:
    if state.get("running"):
        return
    state.update({"running": True, "phase": "시작", "message": "프로필 자동반영 시작", "checked": 0, "fixed": 0, "errors": 0, "log": []})
    try:
        data = safe_data()
        players = data.get("players", [])
        invalid = 0
        for p in players:
            if clean_bad_profile_fields(p):
                invalid += 1
        log(f"임시값/불가능값 {invalid}개 정리", "정리")

        targets = [p for p in players if not complete_profile(p)]
        state["target_total"] = len(targets)
        log(f"보강 대상 {len(targets)}명 확인", "대상확인")

        fixed = checked = errors = 0
        for idx, p in enumerate(targets, 1):
            before = json.dumps(p, ensure_ascii=False, sort_keys=True)
            try:
                enrich_player(p)
            except Exception as e:
                errors += 1
                log(f"{p.get('name')} 처리 오류: {e}", "오류")
            after = json.dumps(p, ensure_ascii=False, sort_keys=True)
            checked += 1
            if before != after and complete_profile(p):
                fixed += 1

            state.update({"checked": checked, "fixed": fixed, "errors": errors, "phase": "진행"})
            if checked % 5 == 0 or checked == len(targets):
                save_data(data)
                log(f"{checked}/{len(targets)}명 확인, {fixed}명 보강, 오류 {errors}건", "진행")
            time.sleep(0.05)

        save_data(data)
        state.update({"running": False, "phase": "완료", "checked": checked, "fixed": fixed, "errors": errors, "last_success": now()})
        log(f"완료: {checked}명 확인, {fixed}명 보강, 오류 {errors}건", "완료")
    except Exception as e:
        state.update({"running": False, "phase": "오류", "error": str(e), "errors": state.get("errors", 0) + 1})
        log(f"치명 오류: {e}\n{traceback.format_exc()}", "오류")

@APP.after_request
def no_cache(resp):
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

@APP.get("/")
def home():
    return render_template("index.html")

@APP.get("/api/data")
def api_data():
    data = safe_data()
    return jsonify(data)

@APP.get("/api/status")
def api_status():
    data = safe_data()
    update_status = data.get("metadata", {}).get("update_status", {})
    if not isinstance(update_status, dict):
        update_status = {}
    if not update_status.get("last_success_at_display"):
        update_status = {**update_status, "last_success_at_display": datetime.now().strftime("%Y-%m-%d %H:%M")}
    return jsonify({
        **state,
        "version": APP_VERSION,
        "port": APP_PORT,
        "counts": counts(data.get("players", [])),
        "update_status": update_status,
    })

@APP.post("/api/admin/login")
def api_admin_login():
    body = request.get_json(silent=True) or {}
    password = str(body.get("password", ""))
    if _verify_admin_password(password):
        token = secrets.token_urlsafe(32)
        ADMIN_TOKENS.add(token)
        log("관리자 로그인 성공", "관리자")
        return jsonify({"ok": True, "token": token, "message": "관리자 로그인 성공"})
    log("관리자 로그인 실패", "관리자")
    return jsonify({"ok": False, "message": "비밀번호가 올바르지 않습니다."}), 401


@APP.post("/api/admin/logout")
def api_admin_logout():
    token = request.headers.get("X-Admin-Token", "").strip()
    if token in ADMIN_TOKENS:
        ADMIN_TOKENS.discard(token)
    return jsonify({"ok": True, "message": "로그아웃 완료"})


@APP.get("/api/admin/check")
def api_admin_check():
    return jsonify({"ok": _admin_token_ok()})


@APP.post("/api/admin/change-password")
def api_admin_change_password():
    if not _admin_token_ok():
        return _admin_required_response()
    body = request.get_json(silent=True) or {}
    current_password = str(body.get("current_password", ""))
    new_password = str(body.get("new_password", ""))
    if not _verify_admin_password(current_password):
        return jsonify({"ok": False, "message": "현재 비밀번호가 올바르지 않습니다."}), 401
    if len(new_password) < 8:
        return jsonify({"ok": False, "message": "새 비밀번호는 8자 이상이어야 합니다."}), 400
    _set_admin_password(new_password)
    ADMIN_TOKENS.clear()
    token = secrets.token_urlsafe(32)
    ADMIN_TOKENS.add(token)
    log("관리자 비밀번호 변경 완료", "관리자")
    return jsonify({"ok": True, "token": token, "message": "비밀번호가 변경되었습니다."})


@APP.post("/api/repair-profiles")
def api_repair_profiles():
    if not _admin_token_ok():
        return _admin_required_response()
    if state.get("running"):
        return jsonify({"ok": True, "message": "이미 실행 중"})
    threading.Thread(target=repair_worker, daemon=True).start()
    return jsonify({"ok": True, "message": "프로필 자동반영 시작"})

@APP.post("/api/reset")
def api_reset():
    if not _admin_token_ok():
        return _admin_required_response()
    shutil.copy(SEED, CURRENT)
    log("데이터를 기본값으로 복구", "초기화")
    return jsonify({"ok": True})

@APP.get("/api/export")
def api_export():
    if not _admin_token_ok():
        return _admin_required_response()
    return send_file(CURRENT, as_attachment=True, download_name="scoutflow_v59_data.json")


@APP.get("/api/k12-check")
def api_k12_check():
    data = safe_data()
    out = {}
    for comp in ["K리그1", "K리그2"]:
        rows = [p for p in data.get("players", []) if p.get("competition") == comp]
        missing = []
        for p in rows:
            if not (display_position_value(p) and valid_height(p.get("height_cm")) and valid_birth(p.get("birthdate"))):
                missing.append({"name": p.get("name"), "team": p.get("team")})
        out[comp] = {"total": len(rows), "missing": len(missing), "examples": missing[:10]}
    return jsonify({"ok": all(v["missing"] == 0 for v in out.values()), "by_league": out})

@APP.get("/api/diagnose")
def api_diagnose():
    data = safe_data()
    return jsonify({
        "version": APP_VERSION,
        "port": APP_PORT,
        "data_counts": counts(data.get("players", [])),
        "server_status": state,
        "paths": {
            "base": str(BASE),
            "current_exists": CURRENT.exists(),
            "seed_exists": SEED.exists(),
            "logs_exists": LOG_DIR.exists(),
        }
    })

if __name__ == "__main__":
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log(f"{APP_VERSION} 서버 시작, 포트 {APP_PORT}, host {BIND_HOST}", "서버")
    APP.run(host=BIND_HOST, port=APP_PORT, debug=False, use_reloader=False)
