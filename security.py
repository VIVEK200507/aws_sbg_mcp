"""Input checks. Every value coming from the AI/user passes through here."""
import re
import uuid


class InputRejected(ValueError):
    """Raised with a message that is safe to show to the user."""


# Obvious SQL attack shapes. This is a SECOND lock: the real protection is that
# no tool accepts SQL and every query is fixed and read-only.
_SQL_PATTERN = re.compile(
    r"(;|--|/\*|\*/"
    r"|\bunion\s+(all\s+)?select\b"
    r"|\bselect\b.+\bfrom\b"
    r"|\binsert\s+into\b"
    r"|\bdelete\s+from\b"
    r"|\bupdate\b.+\bset\b"
    r"|\bdrop\s+(table|database|schema|view|role|user)\b"
    r"|\balter\s+(table|role|user|database)\b"
    r"|\btruncate\b"
    r"|\bcreate\s+(table|role|user|database|function)\b"
    r"|\bgrant\s+(select|insert|update|delete|all)\b"
    r"|\bexec(ute)?\s*\()",
    re.IGNORECASE,
)

# Letters, digits (any language), space, - . ' &   (no underscore, * % , ( ) etc.)
_SAFE_TEXT = re.compile(r"^(?:[^\W_]|[ \-.'&])+$")
_CERT_ID = re.compile(r"^[A-Za-z0-9\-]{3,64}$")


def check_text(value, max_len: int = 60):
    """Validate free text such as a search phrase or event type."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise InputRejected("Invalid input.")
    value = value.strip()
    if not value:
        return None
    if len(value) > max_len:
        raise InputRejected("Input is too long.")
    if _SQL_PATTERN.search(value):
        raise InputRejected("SQL commands are not allowed.")
    if not _SAFE_TEXT.match(value):
        raise InputRejected("Input contains characters that are not allowed.")
    return value


def check_uuid(value) -> str:
    try:
        return str(uuid.UUID(str(value).strip()))
    except (ValueError, AttributeError):
        raise InputRejected("Invalid id. Please use the id returned by the events list.")


def check_certificate_id(value) -> str:
    value = str(value or "").strip()
    if not _CERT_ID.match(value):
        raise InputRejected("Invalid certificate id format.")
    return value


def clamp_limit(limit, default: int = 10, maximum: int = 20) -> int:
    try:
        n = int(limit)
    except (TypeError, ValueError):
        return default
    return max(1, min(n, maximum))
