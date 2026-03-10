# -*- coding: utf-8 -*-
"""
Общий контракт лицензирования VignetteCloud.
"""

STATUS_FREE = "Free"
STATUS_ACTIVE = "Active"
STATUS_EXPIRED = "Expired"
STATUS_BLOCKED = "Blocked"

ERROR_INVALID_KEY = "invalid_key"
ERROR_KEY_NOT_FOUND = "key_not_found"
ERROR_KEY_BLOCKED = "key_blocked"
ERROR_KEY_EXPIRED = "key_expired"
ERROR_BLOCKED = "blocked"
ERROR_EXPIRED = "expired"
ERROR_HWID_MISMATCH = "hwid_mismatch"
ERROR_NO_EMAIL = "no_email"
ERROR_UNKNOWN_STATUS = "unknown_status"
ERROR_CLIENT_NOT_CONFIGURED = "client_not_configured"
ERROR_INVALID_SESSION = "invalid_session"
ERROR_SESSION_EXPIRED = "session_expired"
ERROR_SESSION_REVOKED = "session_revoked"
ERROR_ACCESS_TOKEN_EXPIRED = "access_token_expired"
ERROR_REFRESH_EXPIRED = "refresh_expired"

CLIENT_ERROR_CODE_MAP = {
    ERROR_KEY_NOT_FOUND: ERROR_INVALID_KEY,
    ERROR_KEY_BLOCKED: ERROR_BLOCKED,
    ERROR_KEY_EXPIRED: ERROR_EXPIRED,
}


def normalize_client_error_code(error_code: str | None) -> str | None:
    if not error_code:
        return error_code
    return CLIENT_ERROR_CODE_MAP.get(error_code, error_code)