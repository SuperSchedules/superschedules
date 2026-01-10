"""
Error codes for the chat service.

These codes are machine-readable and can be used by frontends to handle
specific error conditions (e.g., token refresh, rate limiting).
"""

from enum import Enum


class ChatErrorCode(str, Enum):
    """Machine-readable error codes for chat service errors."""

    # Authentication errors (4xx)
    TOKEN_EXPIRED = "token_expired"  # JWT has expired, refresh and retry
    TOKEN_INVALID = "token_invalid"  # JWT is malformed or signature invalid
    AUTH_REQUIRED = "auth_required"  # No token provided
    AUTH_FAILED = "auth_failed"  # User not found or deactivated

    # Rate limiting (429)
    RATE_LIMITED = "rate_limited"  # Too many requests, wait and retry

    # Service errors (5xx)
    LLM_UNAVAILABLE = "llm_unavailable"  # LLM service is down
    LLM_TIMEOUT = "llm_timeout"  # LLM response timed out
    RAG_ERROR = "rag_error"  # RAG/embedding service error
    LOCATION_ERROR = "location_error"  # Location resolution failed

    # General errors
    SERVER_ERROR = "server_error"  # Internal server error
    INVALID_REQUEST = "invalid_request"  # Bad request format
    SESSION_ERROR = "session_error"  # Session creation/lookup failed


# HTTP status code mapping for each error
ERROR_STATUS_CODES = {
    ChatErrorCode.TOKEN_EXPIRED: 401,
    ChatErrorCode.TOKEN_INVALID: 401,
    ChatErrorCode.AUTH_REQUIRED: 401,
    ChatErrorCode.AUTH_FAILED: 401,
    ChatErrorCode.RATE_LIMITED: 429,
    ChatErrorCode.LLM_UNAVAILABLE: 503,
    ChatErrorCode.LLM_TIMEOUT: 504,
    ChatErrorCode.RAG_ERROR: 503,
    ChatErrorCode.LOCATION_ERROR: 500,
    ChatErrorCode.SERVER_ERROR: 500,
    ChatErrorCode.INVALID_REQUEST: 400,
    ChatErrorCode.SESSION_ERROR: 500,
}


def get_status_code(error_code: ChatErrorCode) -> int:
    """Get HTTP status code for an error code."""
    return ERROR_STATUS_CODES.get(error_code, 500)
