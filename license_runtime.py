# -*- coding: utf-8 -*-
"""
Общие runtime-правила лицензирования для клиентской части VignetteCloud.

Этот модуль не зависит от FastAPI/gspread и может использоваться
из `VignetteCore/auth.py` и `auth_guard.py`.
"""

import hashlib
import json
import os
import platform
import subprocess
import urllib.request
from datetime import date

from license_policy import PLAN_APPS, PLAN_LABELS


def normalize_plan(plan: str | None) -> str:
    value = (plan or "demo").strip().lower()
    return value if value in PLAN_APPS else "demo"


def get_plan_label(plan: str | None) -> str:
    normalized = normalize_plan(plan)
    return PLAN_LABELS.get(normalized, normalized)


def app_allowed(plan: str | None, app_name: str) -> bool:
    return app_name in PLAN_APPS.get(normalize_plan(plan), set())


def get_hwid() -> str:
    """Windows-first HWID: UUID -> SHA-256[:32], с fallback на host fingerprint."""
    try:
        out = subprocess.check_output(
            "wmic csproduct get uuid",
            shell=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).decode("utf-8", errors="replace").strip()
        lines = [
            line.strip()
            for line in out.splitlines()
            if line.strip() and line.strip().upper() != "UUID"
        ]
        if lines:
            return hashlib.sha256(lines[0].encode()).hexdigest()[:32]
    except Exception:
        pass

    raw = platform.node() + platform.processor()
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def get_pc_name() -> str:
    return platform.node() or os.environ.get("COMPUTERNAME", "Unknown")


def get_real_today(get_server_date_func) -> date | None:
    """
    Получить дату только из сетевых источников.

    `get_server_date_func` должен возвращать `date | None`.
    """
    server_date = get_server_date_func()
    if server_date is not None:
        return server_date

    try:
        resp = urllib.request.urlopen(
            "https://timeapi.io/api/time/current/zone?timeZone=UTC",
            timeout=5,
        )
        data = json.loads(resp.read())
        return date(data["year"], data["month"], data["day"])
    except Exception:
        pass

    try:
        req = urllib.request.Request("https://www.google.com", method="HEAD")
        resp = urllib.request.urlopen(req, timeout=5)
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(resp.headers["Date"])
        return dt.date()
    except Exception:
        pass

    try:
        resp = urllib.request.urlopen(
            "https://worldtimeapi.org/api/timezone/Etc/UTC",
            timeout=5,
        )
        data = json.loads(resp.read())
        return date.fromisoformat(data["datetime"][:10])
    except Exception:
        pass

    return None