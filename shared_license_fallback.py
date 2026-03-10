from __future__ import annotations


ERROR_INVALID_KEY = "invalid_key"
ERROR_KEY_NOT_FOUND = "key_not_found"
ERROR_KEY_BLOCKED = "key_blocked"
ERROR_KEY_EXPIRED = "key_expired"
ERROR_HWID_MISMATCH = "hwid_mismatch"
ERROR_NO_EMAIL = "no_email"
ERROR_UNKNOWN_STATUS = "unknown_status"

STATUS_FREE = "Free"
STATUS_ACTIVE = "Active"
STATUS_EXPIRED = "Expired"
STATUS_BLOCKED = "Blocked"

try:
    from license_policy import PLAN_APPS, PLAN_LABELS
    from license_runtime import get_plan_label, normalize_plan
except ImportError:
    PLAN_APPS = {
        "demo": set(),
        "basic": {
            "VignetteNamer",
            "FaceSorter",
            "VignetteCropper",
            "AcneRemover",
            "VignetteFiller",
            "SpreadLayout",
            "IndividualFiller",
            "ExportCovers",
        },
        "pro": {
            "VignetteConstructorPro",
            "AcneRemover",
            "VignetteCropper",
            "FaceSorter",
            "SpreadConstructor",
            "VignetteNamer",
            "VignetteFiller",
            "SpreadLayout",
            "IndividualFiller",
            "ExportCovers",
        },
    }

    PLAN_LABELS = {"demo": "Demo", "basic": "Basic", "pro": "Pro"}

    def normalize_plan(plan: str | None) -> str:
        value = (plan or "demo").strip().lower()
        return value if value in PLAN_APPS else "demo"


    def get_plan_label(plan: str | None) -> str:
        normalized = normalize_plan(plan)
        return PLAN_LABELS.get(normalized, normalized)


def make_error_response(error_code: str, error: str, **extra) -> dict:
    payload = {"ok": False, "error_code": error_code, "error": error}
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