# -*- coding: utf-8 -*-
"""
Общий формат ответов License API для клиента и сервера.
"""

from license_contract import STATUS_ACTIVE, normalize_client_error_code


def make_error_response(error_code: str, error: str, **extra) -> dict:
    payload = {
        "ok": False,
        "error_code": error_code,
        "error": error,
    }
    payload.update(extra)
    return payload


def make_verify_success_response(
    plan: str,
    status: str,
    expiration: str,
    days_left,
    server_date: str,
    plan_label: str,
) -> dict:
    return {
        "ok": True,
        "valid": True,
        "plan": plan,
        "plan_label": plan_label,
        "status": status or STATUS_ACTIVE,
        "expiration": expiration,
        "days_left": days_left,
        "server_date": server_date,
    }


def make_activate_success_response(
    plan: str,
    expiration: str,
    days_left,
    message: str,
    server_date: str,
    plan_label: str,
    status: str = STATUS_ACTIVE,
) -> dict:
    return {
        "ok": True,
        "success": True,
        "plan": plan,
        "plan_label": plan_label,
        "status": status,
        "expiration": expiration,
        "days_left": days_left,
        "message": message,
        "server_date": server_date,
    }


def make_session_success_response(
    plan: str,
    status: str,
    expiration: str,
    days_left,
    server_date: str,
    plan_label: str,
    access_token: str,
    access_expires_at: str,
    refresh_token: str | None,
    refresh_expires_at: str,
    session_id: str,
) -> dict:
    payload = {
        "ok": True,
        "valid": True,
        "plan": plan,
        "plan_label": plan_label,
        "status": status or STATUS_ACTIVE,
        "expiration": expiration,
        "days_left": days_left,
        "server_date": server_date,
        "access_token": access_token,
        "access_expires_at": access_expires_at,
        "refresh_expires_at": refresh_expires_at,
        "session_id": session_id,
    }
    if refresh_token is not None:
        payload["refresh_token"] = refresh_token
    return payload


def normalize_api_response(payload: dict | None) -> dict | None:
    if not isinstance(payload, dict):
        return payload

    result = dict(payload)
    raw_code = result.get("error_code")
    if raw_code:
        normalized = normalize_client_error_code(raw_code)
        if normalized != raw_code:
            result["server_error_code"] = raw_code
            result["error_code"] = normalized

    if "ok" not in result:
        if "success" in result:
            result["ok"] = bool(result.get("success"))
        elif "valid" in result:
            result["ok"] = bool(result.get("valid"))
        elif result.get("error_code"):
            result["ok"] = False

    if result.get("ok"):
        result.setdefault("status", STATUS_ACTIVE)

    return result