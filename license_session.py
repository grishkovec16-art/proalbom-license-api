# -*- coding: utf-8 -*-
"""
Утилиты для session-based лицензирования.

Сервер подписывает короткоживущие access token, а клиент хранит только
временные сессионные токены, без общего API-секрета Render.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso_utc(value: str) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).astimezone(timezone.utc)
    except Exception:
        return None


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def generate_session_id() -> str:
    return secrets.token_hex(16)


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))


def create_access_token(
    signing_secret: str,
    session_id: str,
    key: str,
    hwid: str,
    plan: str,
    lifetime_minutes: int,
) -> tuple[str, str]:
    now = utc_now()
    expires_at = now + timedelta(minutes=lifetime_minutes)
    payload = {
        "typ": "access",
        "sid": session_id,
        "key": key,
        "hwid": hwid,
        "plan": plan,
        "iat": iso_utc(now),
        "exp": iso_utc(expires_at),
    }
    body = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(signing_secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{signature}", iso_utc(expires_at)


def verify_access_token(signing_secret: str, token: str) -> tuple[dict | None, str | None]:
    if not token or "." not in token:
        return None, "invalid_session"

    try:
        body, signature = token.rsplit(".", 1)
    except ValueError:
        return None, "invalid_session"

    expected = hmac.new(signing_secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None, "invalid_session"

    try:
        payload = json.loads(_b64url_decode(body).decode("utf-8"))
    except Exception:
        return None, "invalid_session"

    if payload.get("typ") != "access":
        return None, "invalid_session"

    exp = parse_iso_utc(str(payload.get("exp", "")))
    if exp is None:
        return None, "invalid_session"
    if utc_now() >= exp:
        return None, "access_token_expired"

    return payload, None