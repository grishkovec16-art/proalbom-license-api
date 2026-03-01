# -*- coding: utf-8 -*-
"""
Pro.Альбом — License API Server
Промежуточный сервер лицензий: клиент → HTTPS → API → Google Sheets.
Деплой: Render.com (или любой Python-хостинг).

Endpoints:
  GET  /api/health    — проверка работоспособности
  POST /api/verify    — проверка лицензии (key + hwid)
  POST /api/activate  — активация ключа  (key + hwid + email)
"""

import os
import json
import time
import logging
from datetime import date, timedelta, timezone, datetime

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import gspread


# ═══════════════════════════════════════════════════════════════
#  LOGGING
# ═══════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("license-api")


# ═══════════════════════════════════════════════════════════════
#  CONFIG  (из переменных окружения Render)
# ═══════════════════════════════════════════════════════════════
API_SECRET       = os.environ.get("API_SECRET", "")
SPREADSHEET_NAME = os.environ.get("SPREADSHEET_NAME", "VignetteCloud_Licenses")


# ═══════════════════════════════════════════════════════════════
#  SHEET COLUMNS  (1-indexed, как в gspread)
#  A=Key  B=Plan  C=Status  D=HWID  E=ActivationDate
#  F=DurationDays  G=ExpirationDate  H=PCName  I=Email
# ═══════════════════════════════════════════════════════════════
COL_KEY      = 1
COL_PLAN     = 2
COL_STATUS   = 3
COL_HWID     = 4
COL_ACT_DATE = 5
COL_DURATION = 6
COL_EXP_DATE = 7
COL_PC_NAME  = 8
COL_EMAIL    = 9


# ═══════════════════════════════════════════════════════════════
#  PLAN → ALLOWED APPS  (зеркало клиента)
# ═══════════════════════════════════════════════════════════════
PLAN_APPS = {
    "demo":  set(),
    "basic": {"VignetteNamer", "FaceSorter", "VignetteCropper", "AcneRemover",
              "VignetteFiller", "SpreadLayout", "IndividualFiller", "ExportCovers"},
    "pro":   {"VignetteConstructorPro", "AcneRemover", "VignetteCropper",
              "FaceSorter", "SpreadConstructor", "VignetteNamer",
              "VignetteFiller", "SpreadLayout", "IndividualFiller", "ExportCovers"},
}
PLAN_LABELS = {"demo": "Demo", "basic": "Basic", "pro": "Pro"}


# ═══════════════════════════════════════════════════════════════
#  GOOGLE SHEETS CONNECTION  (кеш подключения на 5 мин)
# ═══════════════════════════════════════════════════════════════
_gc = None
_ws_cache = None
_ws_cache_time = 0.0
_WS_CACHE_TTL = 300  # секунд


def _get_gc():
    """Создать / вернуть gspread-клиент из env-переменной."""
    global _gc
    if _gc is not None:
        return _gc
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
    if not creds_json:
        raise RuntimeError("GOOGLE_CREDENTIALS_JSON env var not set")
    # Поддержка base64-кодирования (обход проблем с \n в Render UI)
    import base64
    if not creds_json.startswith("{"):
        try:
            creds_json = base64.b64decode(creds_json).decode("utf-8")
        except Exception:
            pass
    creds = json.loads(creds_json)
    _gc = gspread.service_account_from_dict(creds)
    return _gc


def _open_sheet():
    """Открыть рабочий лист (с кешированием 5 мин)."""
    global _ws_cache, _ws_cache_time, _gc
    now = time.time()
    if _ws_cache is not None and (now - _ws_cache_time) < _WS_CACHE_TTL:
        return _ws_cache
    try:
        gc = _get_gc()
        sh = gc.open(SPREADSHEET_NAME)
        _ws_cache = sh.sheet1
        _ws_cache_time = now
        return _ws_cache
    except Exception:
        _gc = None
        _ws_cache = None
        raise


def _today_utc() -> date:
    """Текущая дата UTC — серверу доверяем (NTP-синхронизация)."""
    return datetime.now(timezone.utc).date()


def _read_row(ws, row: int) -> list:
    """Прочитать строку и дополнить до 9 колонок."""
    vals = ws.row_values(row)
    while len(vals) < 9:
        vals.append("")
    return vals


# ═══════════════════════════════════════════════════════════════
#  PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════════
class VerifyRequest(BaseModel):
    key: str
    hwid: str


class ActivateRequest(BaseModel):
    key: str
    hwid: str
    pc_name: str = ""
    email: str = ""


# ═══════════════════════════════════════════════════════════════
#  FASTAPI APP
# ═══════════════════════════════════════════════════════════════
app = FastAPI(
    title="Pro.Альбом License API",
    docs_url=None,
    redoc_url=None,
)


def _check_auth(auth_header: str):
    """Проверить Bearer-токен (если API_SECRET задан)."""
    if not API_SECRET:
        return
    if auth_header != f"Bearer {API_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")


# ═══════════════════════════════════════════════════════════════
#  GET /api/health  — для мониторинга (UptimeRobot, cron, etc.)
# ═══════════════════════════════════════════════════════════════
@app.get("/api/health")
def health():
    return {"status": "ok", "date": str(_today_utc())}


# ═══════════════════════════════════════════════════════════════
#  POST /api/verify  — проверка лицензии
# ═══════════════════════════════════════════════════════════════
@app.post("/api/verify")
def verify(req: VerifyRequest, authorization: str = Header("")):
    """
    Проверить ключ + HWID в Google Sheets.
    Возвращает {valid, plan, status, expiration, days_left, server_date}
    или {valid: false, error_code, error}.
    """
    _check_auth(authorization)
    key  = req.key.strip().upper()
    hwid = req.hwid.strip()

    if not key:
        return {"valid": False, "error_code": "invalid_key",
                "error": "Пустой ключ"}

    today = _today_utc()

    # ── Открываем таблицу ──
    try:
        ws = _open_sheet()
    except Exception as e:
        logger.error("Sheet open failed: %s", e)
        raise HTTPException(503, "Сервер лицензий временно недоступен")

    # ── Ищем ключ ──
    try:
        cell = ws.find(key, in_column=COL_KEY)
    except Exception:
        cell = None

    if cell is None:
        return {"valid": False, "error_code": "key_not_found",
                "error": "Ключ не найден в системе."}

    # ── Читаем строку ──
    row = cell.row
    try:
        vals = _read_row(ws, row)
    except Exception as e:
        logger.error("Row read failed: %s", e)
        raise HTTPException(503, "Сервер лицензий временно недоступен")

    db_plan   = (vals[COL_PLAN - 1] or "demo").strip().lower()
    db_status = (vals[COL_STATUS - 1] or "").strip()
    db_hwid   = (vals[COL_HWID - 1] or "").strip()
    db_exp    = (vals[COL_EXP_DATE - 1] or "").strip()

    # ── Статус ──
    if db_status == "Blocked":
        return {"valid": False, "error_code": "key_blocked",
                "error": "Лицензия заблокирована администратором."}

    if db_status == "Expired":
        return {"valid": False, "error_code": "key_expired",
                "error": "Срок действия лицензии истёк."}

    # ── HWID ──
    if db_hwid and db_hwid != hwid:
        return {"valid": False, "error_code": "hwid_mismatch",
                "error": "Лицензия привязана к другому компьютеру."}

    # ── Проверка срока (серверная дата) ──
    if db_exp:
        try:
            exp_date = date.fromisoformat(db_exp)
            if today > exp_date:
                try:
                    ws.update_cell(row, COL_STATUS, "Expired")
                except Exception:
                    pass
                return {"valid": False, "error_code": "key_expired",
                        "error": f"Срок действия лицензии истёк ({db_exp})."}
        except ValueError:
            pass

    # ── Всё ОК ──
    plan = db_plan if db_plan in PLAN_APPS else "demo"
    days_left = None
    if db_exp:
        try:
            days_left = (date.fromisoformat(db_exp) - today).days
        except ValueError:
            pass

    logger.info("Verify OK: key=%s plan=%s", key[:6] + "...", plan)
    return {
        "valid": True,
        "plan": plan,
        "plan_label": PLAN_LABELS.get(plan, plan),
        "status": db_status,
        "expiration": db_exp,
        "days_left": days_left,
        "server_date": str(today),
    }


# ═══════════════════════════════════════════════════════════════
#  POST /api/activate  — активация ключа
# ═══════════════════════════════════════════════════════════════
@app.post("/api/activate")
def activate(req: ActivateRequest, authorization: str = Header("")):
    """
    Активировать лицензионный ключ.
    Free  → Active: привязка HWID, запись даты/email.
    Active → повторный вход: проверка HWID, обновление PC name.
    """
    _check_auth(authorization)
    key     = req.key.strip().upper()
    hwid    = req.hwid.strip()
    pc_name = req.pc_name.strip()
    email   = req.email.strip()

    if not key:
        return {"success": False, "error_code": "invalid_key",
                "error": "Введите лицензионный ключ"}
    if not email:
        return {"success": False, "error_code": "no_email",
                "error": "Введите email"}

    today = _today_utc()

    # ── Открываем таблицу ──
    try:
        ws = _open_sheet()
    except Exception as e:
        logger.error("Sheet open failed: %s", e)
        raise HTTPException(503, "Сервер лицензий временно недоступен")

    # ── Ищем ключ ──
    try:
        cell = ws.find(key, in_column=COL_KEY)
    except Exception:
        cell = None
    if cell is None:
        return {"success": False, "error_code": "key_not_found",
                "error": "Ключ не найден"}

    # ── Читаем строку ──
    row = cell.row
    try:
        vals = _read_row(ws, row)
    except Exception as e:
        logger.error("Row read failed: %s", e)
        raise HTTPException(503, "Сервер лицензий временно недоступен")

    db_plan   = (vals[COL_PLAN - 1] or "demo").strip().lower()
    db_status = (vals[COL_STATUS - 1] or "Free").strip()
    db_hwid   = (vals[COL_HWID - 1] or "").strip()
    db_dur    = vals[COL_DURATION - 1] or "0"
    db_exp    = (vals[COL_EXP_DATE - 1] or "").strip()

    if db_status == "Blocked":
        return {"success": False, "error_code": "key_blocked",
                "error": "Ключ заблокирован администратором"}
    if db_status == "Expired":
        return {"success": False, "error_code": "key_expired",
                "error": "Срок действия ключа истёк"}

    plan = db_plan if db_plan in PLAN_APPS else "demo"

    # ══════════════════════════════════════════════════════════
    #  FREE → первая активация
    # ══════════════════════════════════════════════════════════
    if db_status == "Free":
        try:
            dur = int(db_dur)
        except ValueError:
            dur = 0
        exp_str = str(today + timedelta(days=dur)) if dur > 0 else ""

        # Batch-запись в одном API-вызове
        cells = [
            gspread.Cell(row, COL_STATUS,   "Active"),
            gspread.Cell(row, COL_HWID,     hwid),
            gspread.Cell(row, COL_ACT_DATE, str(today)),
            gspread.Cell(row, COL_PC_NAME,  pc_name),
            gspread.Cell(row, COL_EMAIL,    email),
        ]
        if exp_str:
            cells.append(gspread.Cell(row, COL_EXP_DATE, exp_str))

        try:
            ws.update_cells(cells)
        except Exception as e:
            logger.error("Sheet write failed: %s", e)
            raise HTTPException(503, "Не удалось записать данные лицензии")

        days_left = None
        if exp_str:
            try:
                days_left = (date.fromisoformat(exp_str) - today).days
            except ValueError:
                pass

        logger.info("Activated: key=%s plan=%s", key[:6] + "...", plan)
        return {
            "success": True,
            "plan": plan,
            "plan_label": PLAN_LABELS.get(plan, plan),
            "expiration": exp_str,
            "days_left": days_left,
            "message": f"Ключ активирован!\nПлан: {PLAN_LABELS.get(plan, plan)}",
            "server_date": str(today),
        }

    # ══════════════════════════════════════════════════════════
    #  ACTIVE → повторный вход / верификация
    # ══════════════════════════════════════════════════════════
    if db_status == "Active":
        if db_hwid and db_hwid != hwid:
            return {"success": False, "error_code": "hwid_mismatch",
                    "error": "Ключ привязан к другому компьютеру.\n"
                             "Обратитесь в поддержку для переноса."}

        # Проверка срока
        if db_exp:
            try:
                exp_date = date.fromisoformat(db_exp)
                if today > exp_date:
                    try:
                        ws.update_cell(row, COL_STATUS, "Expired")
                    except Exception:
                        pass
                    return {"success": False, "error_code": "key_expired",
                            "error": f"Срок действия ключа истёк ({db_exp})"}
            except ValueError:
                pass

        # Обновить PC name если изменилось
        old_pc = (vals[COL_PC_NAME - 1] or "").strip()
        if pc_name and old_pc != pc_name:
            try:
                ws.update_cell(row, COL_PC_NAME, pc_name)
            except Exception:
                pass

        days_left = None
        if db_exp:
            try:
                days_left = (date.fromisoformat(db_exp) - today).days
            except ValueError:
                pass

        dur_msg = ""
        if days_left is not None:
            dur_msg = f"\nОсталось {days_left} дн."

        logger.info("Re-login: key=%s plan=%s", key[:6] + "...", plan)
        return {
            "success": True,
            "plan": plan,
            "plan_label": PLAN_LABELS.get(plan, plan),
            "expiration": db_exp,
            "days_left": days_left,
            "message": f"Добро пожаловать!\n"
                       f"План: {PLAN_LABELS.get(plan, plan)}{dur_msg}",
            "server_date": str(today),
        }

    return {"success": False, "error_code": "unknown_status",
            "error": f"Неизвестный статус ключа: {db_status}"}


# ═══════════════════════════════════════════════════════════════
#  GLOBAL ERROR HANDLER
# ═══════════════════════════════════════════════════════════════
@app.exception_handler(Exception)
async def generic_handler(request: Request, exc: Exception):
    logger.error("Unhandled: %s: %s", type(exc).__name__, exc)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error"},
    )
