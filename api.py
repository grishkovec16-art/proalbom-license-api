# -*- coding: utf-8 -*-
"""
Pro.Альбом — License API Server
Промежуточный сервер лицензий: клиент → HTTPS → API → Google Sheets.
Деплой: Render.com (или любой Python-хостинг).

Endpoints:
  GET  /api/health                 — проверка работоспособности
  POST /api/verify                 — legacy/admin проверка лицензии (key + hwid)
  POST /api/activate               — legacy/admin активация ключа (key + hwid + email)
  POST /api/client/activate        — публичная активация + выдача session tokens
  POST /api/client/bootstrap       — выдача session tokens для уже активированного ключа
  POST /api/client/refresh         — обновление access token по refresh token
  POST /api/client/session-verify  — проверка access token
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import gspread
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

_ROOT_DIR = Path(__file__).resolve().parent.parent
if str(_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(_ROOT_DIR))


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("license-api")


API_SECRET = os.environ.get("API_SECRET", "")
SESSION_SIGNING_SECRET = os.environ.get("SESSION_SIGNING_SECRET", API_SECRET)
SPREADSHEET_NAME = os.environ.get("SPREADSHEET_NAME", "VignetteCloud_Licenses")
ACCESS_TOKEN_TTL_MINUTES = int(os.environ.get("ACCESS_TOKEN_TTL_MINUTES", "15"))
REFRESH_TOKEN_TTL_DAYS = int(os.environ.get("REFRESH_TOKEN_TTL_DAYS", "30"))


# License sheet columns: A=Key B=Plan C=Status D=HWID E=ActivationDate
# F=DurationDays G=ExpirationDate H=PCName I=Email
COL_KEY = 1
COL_PLAN = 2
COL_STATUS = 3
COL_HWID = 4
COL_ACT_DATE = 5
COL_DURATION = 6
COL_EXP_DATE = 7
COL_PC_NAME = 8
COL_EMAIL = 9


# Sessions sheet columns
S_COL_ID = 1
S_COL_REFRESH_HASH = 2
S_COL_KEY = 3
S_COL_HWID = 4
S_COL_PLAN = 5
S_COL_CREATED_AT = 6
S_COL_LAST_SEEN_AT = 7
S_COL_REFRESH_EXPIRES_AT = 8
S_COL_REVOKED = 9
S_COL_PC_NAME = 10
S_COL_EMAIL = 11
S_COL_VERSION = 12
SESSION_HEADERS = [
    "SessionId",
    "RefreshTokenHash",
    "LicenseKey",
    "HWID",
    "Plan",
    "CreatedAt",
    "LastSeenAt",
    "RefreshExpiresAt",
    "Revoked",
    "PCName",
    "Email",
    "Version",
]


try:
    from license_api_response import (
        make_activate_success_response,
        make_error_response,
        make_session_success_response,
        make_verify_success_response,
    )
    from license_contract import (
        ERROR_ACCESS_TOKEN_EXPIRED,
        ERROR_HWID_MISMATCH,
        ERROR_INVALID_KEY,
        ERROR_INVALID_SESSION,
        ERROR_KEY_BLOCKED,
        ERROR_KEY_EXPIRED,
        ERROR_KEY_NOT_FOUND,
        ERROR_NO_EMAIL,
        ERROR_REFRESH_EXPIRED,
        ERROR_SESSION_EXPIRED,
        ERROR_SESSION_REVOKED,
        ERROR_UNKNOWN_STATUS,
        STATUS_ACTIVE,
        STATUS_BLOCKED,
        STATUS_EXPIRED,
        STATUS_FREE,
    )
    from license_policy import PLAN_APPS, PLAN_LABELS
    from license_runtime import get_plan_label, normalize_plan
    from license_session import (
        create_access_token,
        generate_refresh_token,
        generate_session_id,
        hash_token,
        iso_utc,
        parse_iso_utc,
        utc_now,
        verify_access_token,
    )
except ImportError:
    from shared_license_fallback import (
        ERROR_ACCESS_TOKEN_EXPIRED,
        ERROR_HWID_MISMATCH,
        ERROR_INVALID_KEY,
        ERROR_INVALID_SESSION,
        ERROR_KEY_BLOCKED,
        ERROR_KEY_EXPIRED,
        ERROR_KEY_NOT_FOUND,
        ERROR_NO_EMAIL,
        ERROR_REFRESH_EXPIRED,
        ERROR_SESSION_EXPIRED,
        ERROR_SESSION_REVOKED,
        ERROR_UNKNOWN_STATUS,
        PLAN_APPS,
        PLAN_LABELS,
        STATUS_ACTIVE,
        STATUS_BLOCKED,
        STATUS_EXPIRED,
        STATUS_FREE,
        get_plan_label,
        make_activate_success_response,
        make_error_response,
        make_session_success_response,
        make_verify_success_response,
        normalize_plan,
    )
    from license_session import (
        create_access_token,
        generate_refresh_token,
        generate_session_id,
        hash_token,
        iso_utc,
        parse_iso_utc,
        utc_now,
        verify_access_token,
    )


_gc = None
_license_ws_cache = None
_license_ws_cache_time = 0.0
_sessions_ws_cache = None
_sessions_ws_cache_time = 0.0
_WS_CACHE_TTL = 300


def _get_gc():
    global _gc
    if _gc is not None:
        return _gc

    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
    if not creds_json:
        raise RuntimeError("GOOGLE_CREDENTIALS_JSON env var not set")

    import base64

    if not creds_json.startswith("{"):
        try:
            creds_json = base64.b64decode(creds_json).decode("utf-8")
        except Exception:
            pass

    creds = json.loads(creds_json)
    _gc = gspread.service_account_from_dict(creds)
    return _gc


def _open_spreadsheet():
    gc = _get_gc()
    return gc.open(SPREADSHEET_NAME)


def _open_license_sheet():
    global _license_ws_cache, _license_ws_cache_time, _gc
    now = time.time()
    if _license_ws_cache is not None and (now - _license_ws_cache_time) < _WS_CACHE_TTL:
        return _license_ws_cache
    try:
        ws = _open_spreadsheet().sheet1
        _license_ws_cache = ws
        _license_ws_cache_time = now
        return ws
    except Exception:
        _gc = None
        _license_ws_cache = None
        raise


def _open_sessions_sheet():
    global _sessions_ws_cache, _sessions_ws_cache_time, _gc
    now = time.time()
    if _sessions_ws_cache is not None and (now - _sessions_ws_cache_time) < _WS_CACHE_TTL:
        return _sessions_ws_cache
    try:
        sh = _open_spreadsheet()
        try:
            ws = sh.worksheet("Sessions")
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title="Sessions", rows=2000, cols=12)
            ws.append_row(SESSION_HEADERS)
        _sessions_ws_cache = ws
        _sessions_ws_cache_time = now
        return ws
    except Exception:
        _gc = None
        _sessions_ws_cache = None
        raise


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


def _read_row(ws, row: int, width: int) -> list[str]:
    vals = ws.row_values(row)
    while len(vals) < width:
        vals.append("")
    return vals


def _bool_cell(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _safe_days_left(expiration: str, today: date) -> int | None:
    if not expiration:
        return None
    try:
        return (date.fromisoformat(expiration) - today).days
    except Exception:
        return None


def _check_auth(auth_header: str):
    if not API_SECRET:
        return
    if auth_header != f"Bearer {API_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")


def _find_key_row(ws, key: str):
    try:
        return ws.find(key, in_column=COL_KEY)
    except Exception:
        return None


def _load_license_record(key: str):
    try:
        ws = _open_license_sheet()
    except Exception as exc:
        logger.error("Sheet open failed: %s", exc)
        raise HTTPException(503, "Сервер лицензий временно недоступен")

    cell = _find_key_row(ws, key)
    if cell is None:
        return None, None, None, make_error_response(ERROR_KEY_NOT_FOUND, "Ключ не найден в системе.")

    row = cell.row
    try:
        vals = _read_row(ws, row, 9)
    except Exception as exc:
        logger.error("Row read failed: %s", exc)
        raise HTTPException(503, "Сервер лицензий временно недоступен")

    return ws, row, vals, None


def _validate_active_license(key: str, hwid: str, today: date):
    ws, row, vals, error = _load_license_record(key)
    if error:
        return None, error

    db_plan = (vals[COL_PLAN - 1] or "demo").strip().lower()
    db_status = (vals[COL_STATUS - 1] or "").strip()
    db_hwid = (vals[COL_HWID - 1] or "").strip()
    db_exp = (vals[COL_EXP_DATE - 1] or "").strip()

    if db_status == STATUS_FREE:
        return None, make_error_response(ERROR_INVALID_KEY, "Ключ ещё не активирован.")
    if db_status == STATUS_BLOCKED:
        return None, make_error_response(ERROR_KEY_BLOCKED, "Лицензия заблокирована администратором.")
    if db_status == STATUS_EXPIRED:
        return None, make_error_response(ERROR_KEY_EXPIRED, "Срок действия лицензии истёк.")
    if db_hwid and db_hwid != hwid:
        return None, make_error_response(ERROR_HWID_MISMATCH, "Лицензия привязана к другому компьютеру.")

    if db_exp:
        try:
            exp_date = date.fromisoformat(db_exp)
            if today > exp_date:
                try:
                    ws.update_cell(row, COL_STATUS, STATUS_EXPIRED)
                except Exception:
                    pass
                return None, make_error_response(ERROR_KEY_EXPIRED, f"Срок действия лицензии истёк ({db_exp}).")
        except ValueError:
            pass

    plan = normalize_plan(db_plan)
    return {
        "plan": plan,
        "status": db_status or STATUS_ACTIVE,
        "expiration": db_exp,
        "days_left": _safe_days_left(db_exp, today),
        "pc_name": (vals[COL_PC_NAME - 1] or "").strip(),
        "email": (vals[COL_EMAIL - 1] or "").strip(),
    }, None


def _activate_or_relogin_license(key: str, hwid: str, pc_name: str, email: str, today: date):
    ws, row, vals, error = _load_license_record(key)
    if error:
        return error

    db_plan = (vals[COL_PLAN - 1] or "demo").strip().lower()
    db_status = (vals[COL_STATUS - 1] or STATUS_FREE).strip()
    db_hwid = (vals[COL_HWID - 1] or "").strip()
    db_dur = vals[COL_DURATION - 1] or "0"
    db_exp = (vals[COL_EXP_DATE - 1] or "").strip()
    plan = normalize_plan(db_plan)

    if db_status == STATUS_BLOCKED:
        return make_error_response(ERROR_KEY_BLOCKED, "Ключ заблокирован администратором")
    if db_status == STATUS_EXPIRED:
        return make_error_response(ERROR_KEY_EXPIRED, "Срок действия ключа истёк")

    if db_status == STATUS_FREE:
        if not email:
            return make_error_response(ERROR_NO_EMAIL, "Введите email")
        try:
            dur = int(db_dur)
        except ValueError:
            dur = 0
        exp_str = str(today + timedelta(days=dur)) if dur > 0 else ""
        cells = [
            gspread.Cell(row, COL_STATUS, STATUS_ACTIVE),
            gspread.Cell(row, COL_HWID, hwid),
            gspread.Cell(row, COL_ACT_DATE, str(today)),
            gspread.Cell(row, COL_PC_NAME, pc_name),
            gspread.Cell(row, COL_EMAIL, email),
        ]
        if exp_str:
            cells.append(gspread.Cell(row, COL_EXP_DATE, exp_str))
        try:
            ws.update_cells(cells)
        except Exception as exc:
            logger.error("Sheet write failed: %s", exc)
            raise HTTPException(503, "Не удалось записать данные лицензии")

        days_left = _safe_days_left(exp_str, today)
        return make_activate_success_response(
            plan=plan,
            expiration=exp_str,
            days_left=days_left,
            message=f"Ключ активирован!\nПлан: {get_plan_label(plan)}",
            server_date=str(today),
            plan_label=get_plan_label(plan),
        )

    if db_status == STATUS_ACTIVE:
        if db_hwid and db_hwid != hwid:
            return make_error_response(
                ERROR_HWID_MISMATCH,
                "Ключ привязан к другому компьютеру.\nОбратитесь в поддержку для переноса.",
            )

        if db_exp:
            try:
                exp_date = date.fromisoformat(db_exp)
                if today > exp_date:
                    try:
                        ws.update_cell(row, COL_STATUS, STATUS_EXPIRED)
                    except Exception:
                        pass
                    return make_error_response(ERROR_KEY_EXPIRED, f"Срок действия ключа истёк ({db_exp})")
            except ValueError:
                pass

        old_pc = (vals[COL_PC_NAME - 1] or "").strip()
        if pc_name and old_pc != pc_name:
            try:
                ws.update_cell(row, COL_PC_NAME, pc_name)
            except Exception:
                pass

        days_left = _safe_days_left(db_exp, today)
        dur_msg = f"\nОсталось {days_left} дн." if days_left is not None else ""
        return make_activate_success_response(
            plan=plan,
            expiration=db_exp,
            days_left=days_left,
            message=f"Добро пожаловать!\nПлан: {get_plan_label(plan)}{dur_msg}",
            server_date=str(today),
            plan_label=get_plan_label(plan),
        )

    return make_error_response(ERROR_UNKNOWN_STATUS, f"Неизвестный статус ключа: {db_status}")


def _list_session_rows(ws):
    rows = ws.get_all_values()
    return rows[1:] if len(rows) > 1 else []


def _find_session_by_id(ws, session_id: str):
    for offset, vals in enumerate(_list_session_rows(ws), start=2):
        padded = list(vals) + [""] * (len(SESSION_HEADERS) - len(vals))
        if padded[S_COL_ID - 1] == session_id:
            return offset, padded
    return None, None


def _find_session_by_refresh_hash(ws, refresh_hash: str):
    for offset, vals in enumerate(_list_session_rows(ws), start=2):
        padded = list(vals) + [""] * (len(SESSION_HEADERS) - len(vals))
        if padded[S_COL_REFRESH_HASH - 1] == refresh_hash:
            return offset, padded
    return None, None


def _write_session_row(ws, row: int | None, session_data: dict):
    values = [
        session_data["session_id"],
        session_data["refresh_hash"],
        session_data["key"],
        session_data["hwid"],
        session_data["plan"],
        session_data["created_at"],
        session_data["last_seen_at"],
        session_data["refresh_expires_at"],
        "1" if session_data.get("revoked") else "0",
        session_data.get("pc_name", ""),
        session_data.get("email", ""),
        str(session_data.get("version", 1)),
    ]
    if row is None:
        ws.append_row(values)
    else:
        ws.update(f"A{row}:L{row}", [values])


def _issue_session_payload(
    key: str,
    hwid: str,
    plan: str,
    expiration: str,
    days_left,
    pc_name: str,
    email: str,
    session_id: str | None = None,
    refresh_token: str | None = None,
    refresh_expires_at: str | None = None,
    row: int | None = None,
):
    ws = _open_sessions_sheet()
    now = utc_now()
    session_id = session_id or generate_session_id()
    refresh_token = refresh_token or generate_refresh_token()
    refresh_expires_at = refresh_expires_at or iso_utc(now + timedelta(days=REFRESH_TOKEN_TTL_DAYS))
    access_token, access_expires_at = create_access_token(
        SESSION_SIGNING_SECRET,
        session_id=session_id,
        key=key,
        hwid=hwid,
        plan=plan,
        lifetime_minutes=ACCESS_TOKEN_TTL_MINUTES,
    )

    session_data = {
        "session_id": session_id,
        "refresh_hash": hash_token(refresh_token),
        "key": key,
        "hwid": hwid,
        "plan": plan,
        "created_at": iso_utc(now),
        "last_seen_at": iso_utc(now),
        "refresh_expires_at": refresh_expires_at,
        "revoked": False,
        "pc_name": pc_name,
        "email": email,
        "version": 1,
    }
    if row is not None:
        # preserve original created_at when updating existing session
        _, existing = _find_session_by_id(ws, session_id)
        if existing:
            session_data["created_at"] = existing[S_COL_CREATED_AT - 1] or session_data["created_at"]
    _write_session_row(ws, row, session_data)

    return make_session_success_response(
        plan=plan,
        status=STATUS_ACTIVE,
        expiration=expiration,
        days_left=days_left,
        server_date=str(_today_utc()),
        plan_label=get_plan_label(plan),
        access_token=access_token,
        access_expires_at=access_expires_at,
        refresh_token=refresh_token,
        refresh_expires_at=refresh_expires_at,
        session_id=session_id,
    )


class VerifyRequest(BaseModel):
    key: str
    hwid: str


class ActivateRequest(BaseModel):
    key: str
    hwid: str
    pc_name: str = ""
    email: str = ""


class BootstrapRequest(BaseModel):
    key: str
    hwid: str
    pc_name: str = ""


class RefreshRequest(BaseModel):
    refresh_token: str
    hwid: str
    pc_name: str = ""


class SessionVerifyRequest(BaseModel):
    access_token: str
    hwid: str


app = FastAPI(title="Pro.Альбом License API", docs_url=None, redoc_url=None)


@app.get("/api/health")
def health():
    return {"status": "ok", "date": str(_today_utc())}


@app.post("/api/verify")
def verify(req: VerifyRequest, authorization: str = Header("")):
    _check_auth(authorization)
    key = req.key.strip().upper()
    hwid = req.hwid.strip()
    if not key:
        return make_error_response(ERROR_INVALID_KEY, "Пустой ключ")

    state, error = _validate_active_license(key, hwid, _today_utc())
    if error:
        return error

    logger.info("Verify OK: key=%s plan=%s", key[:6] + "...", state["plan"])
    return make_verify_success_response(
        plan=state["plan"],
        status=state["status"],
        expiration=state["expiration"],
        days_left=state["days_left"],
        server_date=str(_today_utc()),
        plan_label=get_plan_label(state["plan"]),
    )


@app.post("/api/activate")
def activate(req: ActivateRequest, authorization: str = Header("")):
    _check_auth(authorization)
    key = req.key.strip().upper()
    if not key:
        return make_error_response(ERROR_INVALID_KEY, "Введите лицензионный ключ", success=False)
    if not req.email.strip():
        return make_error_response(ERROR_NO_EMAIL, "Введите email", success=False)
    return _activate_or_relogin_license(
        key=key,
        hwid=req.hwid.strip(),
        pc_name=req.pc_name.strip(),
        email=req.email.strip(),
        today=_today_utc(),
    )


@app.post("/api/client/activate")
def client_activate(req: ActivateRequest):
    key = req.key.strip().upper()
    hwid = req.hwid.strip()
    pc_name = req.pc_name.strip()
    email = req.email.strip()
    if not key:
        return make_error_response(ERROR_INVALID_KEY, "Введите лицензионный ключ")
    if not email:
        return make_error_response(ERROR_NO_EMAIL, "Введите email")

    result = _activate_or_relogin_license(key, hwid, pc_name, email, _today_utc())
    if not result.get("ok"):
        return result

    return _issue_session_payload(
        key=key,
        hwid=hwid,
        plan=result["plan"],
        expiration=result.get("expiration", ""),
        days_left=result.get("days_left"),
        pc_name=pc_name,
        email=email,
    )


@app.post("/api/client/bootstrap")
def client_bootstrap(req: BootstrapRequest):
    key = req.key.strip().upper()
    hwid = req.hwid.strip()
    if not key:
        return make_error_response(ERROR_INVALID_KEY, "Введите лицензионный ключ")

    state, error = _validate_active_license(key, hwid, _today_utc())
    if error:
        return error

    logger.info("Bootstrap session: key=%s plan=%s", key[:6] + "...", state["plan"])
    return _issue_session_payload(
        key=key,
        hwid=hwid,
        plan=state["plan"],
        expiration=state["expiration"],
        days_left=state["days_left"],
        pc_name=req.pc_name.strip() or state.get("pc_name", ""),
        email=state.get("email", ""),
    )


@app.post("/api/client/refresh")
def client_refresh(req: RefreshRequest):
    refresh_token = req.refresh_token.strip()
    hwid = req.hwid.strip()
    if not refresh_token:
        return make_error_response(ERROR_INVALID_SESSION, "Refresh token не передан")

    try:
        ws = _open_sessions_sheet()
    except Exception as exc:
        logger.error("Sessions sheet open failed: %s", exc)
        raise HTTPException(503, "Сервер лицензий временно недоступен")

    row, vals = _find_session_by_refresh_hash(ws, hash_token(refresh_token))
    if row is None:
        return make_error_response(ERROR_INVALID_SESSION, "Refresh token не найден")

    if _bool_cell(vals[S_COL_REVOKED - 1]):
        return make_error_response(ERROR_SESSION_REVOKED, "Сессия отозвана")
    if vals[S_COL_HWID - 1] and vals[S_COL_HWID - 1] != hwid:
        return make_error_response(ERROR_HWID_MISMATCH, "Сессия привязана к другому компьютеру")

    refresh_expires_at = vals[S_COL_REFRESH_EXPIRES_AT - 1]
    exp_dt = parse_iso_utc(refresh_expires_at)
    if exp_dt is None or utc_now() >= exp_dt:
        return make_error_response(ERROR_REFRESH_EXPIRED, "Срок действия session token истёк")

    key = vals[S_COL_KEY - 1].strip().upper()
    state, error = _validate_active_license(key, hwid, _today_utc())
    if error:
        return error

    logger.info("Refresh session: key=%s plan=%s", key[:6] + "...", state["plan"])
    return _issue_session_payload(
        key=key,
        hwid=hwid,
        plan=state["plan"],
        expiration=state["expiration"],
        days_left=state["days_left"],
        pc_name=req.pc_name.strip() or vals[S_COL_PC_NAME - 1],
        email=vals[S_COL_EMAIL - 1],
        session_id=vals[S_COL_ID - 1],
        refresh_token=refresh_token,
        refresh_expires_at=refresh_expires_at,
        row=row,
    )


@app.post("/api/client/session-verify")
def client_session_verify(req: SessionVerifyRequest):
    access_token = req.access_token.strip()
    hwid = req.hwid.strip()
    payload, error_code = verify_access_token(SESSION_SIGNING_SECRET, access_token)
    if error_code:
        if error_code == ERROR_ACCESS_TOKEN_EXPIRED:
            return make_error_response(ERROR_ACCESS_TOKEN_EXPIRED, "Срок действия access token истёк")
        return make_error_response(ERROR_INVALID_SESSION, "Access token недействителен")

    if payload.get("hwid") != hwid:
        return make_error_response(ERROR_HWID_MISMATCH, "Сессия привязана к другому компьютеру")

    try:
        ws = _open_sessions_sheet()
    except Exception as exc:
        logger.error("Sessions sheet open failed: %s", exc)
        raise HTTPException(503, "Сервер лицензий временно недоступен")

    row, vals = _find_session_by_id(ws, str(payload.get("sid", "")))
    if row is None:
        return make_error_response(ERROR_INVALID_SESSION, "Сессия не найдена")
    if _bool_cell(vals[S_COL_REVOKED - 1]):
        return make_error_response(ERROR_SESSION_REVOKED, "Сессия отозвана")

    refresh_expires_at = vals[S_COL_REFRESH_EXPIRES_AT - 1]
    exp_dt = parse_iso_utc(refresh_expires_at)
    if exp_dt is None or utc_now() >= exp_dt:
        return make_error_response(ERROR_SESSION_EXPIRED, "Сессия истекла")

    key = vals[S_COL_KEY - 1].strip().upper()
    state, error = _validate_active_license(key, hwid, _today_utc())
    if error:
        return error

    try:
        access_expires_at = str(payload.get("exp", ""))
    except Exception:
        access_expires_at = ""

    ws.update_cell(row, S_COL_LAST_SEEN_AT, iso_utc(utc_now()))
    return make_session_success_response(
        plan=state["plan"],
        status=state["status"],
        expiration=state["expiration"],
        days_left=state["days_left"],
        server_date=str(_today_utc()),
        plan_label=get_plan_label(state["plan"]),
        access_token=access_token,
        access_expires_at=access_expires_at,
        refresh_token=None,
        refresh_expires_at=refresh_expires_at,
        session_id=vals[S_COL_ID - 1],
    )


@app.exception_handler(Exception)
async def generic_handler(request: Request, exc: Exception):
    logger.error("Unhandled: %s: %s", type(exc).__name__, exc)
    return JSONResponse(status_code=500, content={"error": "Internal server error"})