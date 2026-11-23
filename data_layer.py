"""
Read-only data layer (Supabase REST API, GET requests only).

Safety rules enforced here:
  * Only GET exists on the HTTP client (no way to write).
  * Only tables/columns listed in TABLES can be read. Never SELECT *.
  * Private fields (email, phone, roll number...) are blocked twice:
    not in the allowlist, and rejected by a final guard that scans results recursively.
  * User text only ever becomes a filter VALUE, never a table/column/command.
  * site_config is readable only for keys listed in PUBLIC_SITE_CONFIG_KEYS.
"""
import json
import logging
import os
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger("mcp.data")

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
TIMEZONE = ZoneInfo(os.environ.get("TIMEZONE", "Asia/Kolkata"))
CACHE_SECONDS = int(os.environ.get("CACHE_SECONDS", "60"))
MAX_ROWS = 20

PUBLIC_SITE_CONFIG_KEYS = [
    k.strip() for k in os.environ.get("PUBLIC_SITE_CONFIG_KEYS", "").split(",") if k.strip()
]
for _k in PUBLIC_SITE_CONFIG_KEYS:
    if not re.fullmatch(r"[A-Za-z0-9_.\-]+", _k):
        raise RuntimeError(f"Invalid site_config key in PUBLIC_SITE_CONFIG_KEYS: {_k!r}")

# ---- ALLOWLIST: the only tables and columns the MCP server can ever read ----
TABLES = {
    "events": [
        "id", "name", "description", "event_date", "event_end_date",
        "event_timing", "speaker_name", "event_type",
    ],
    "site_config": ["key", "value"],
    "attendance": ["id"],  # only used to COUNT rows; rows are never returned
    "certificates": [
        "certificate_id", "recipient_name_snapshot", "event_name_snapshot",
        "event_date_snapshot", "award_title", "certificate_kind",
        "role_snapshot", "wing_snapshot", "issue_date",
    ],
}

# Final guard: if any of these ever appear in a result, refuse to return it.
BLOCKED = {
    "email", "phone", "phone_number", "mobile", "roll_no", "roll_number",
    "password", "resume_url", "participant_id", "created_by", "uploaded_by",
}
_BLOCKED_KEY_PARTS = ("email", "phone", "password", "resume")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
for _t, _cols in TABLES.items():
    assert not (BLOCKED & set(_cols)), f"Blocked column in allowlist for {_t}"


def _is_blocked_key(key) -> bool:
    k = str(key).lower()
    return k in BLOCKED or any(part in k for part in _BLOCKED_KEY_PARTS)


def assert_safe(obj) -> None:
    """Raise if a result (at any nesting depth) has a private field name or an email address."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if _is_blocked_key(k):
                raise RuntimeError("Sensitive field detected in result")
            assert_safe(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            assert_safe(v)
    elif isinstance(obj, str) and _EMAIL_RE.search(obj):
        raise RuntimeError("Email address detected in result")


class ReadOnlyClient:
    """HTTP client that can only GET. There is intentionally no post/patch/delete."""

    def __init__(self):
        headers = {"apikey": SUPABASE_KEY, "Accept": "application/json"}
        if SUPABASE_KEY.startswith("eyJ"):  # legacy JWT-style keys
            headers["Authorization"] = f"Bearer {SUPABASE_KEY}"
        self._http = httpx.Client(
            base_url=f"{SUPABASE_URL}/rest/v1",
            headers=headers,
            timeout=10.0,
            follow_redirects=False,
        )

    def get(self, path: str, params: dict, headers: dict | None = None):
        resp = self._http.get(path, params=params, headers=headers)
        resp.raise_for_status()
        return resp


_client = ReadOnlyClient()

# ---- tiny in-memory cache (protects the database from repeated questions) ----
_cache: dict = {}


def _cached(key, fn):
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = fn()
    if len(_cache) > 500:
        _cache.clear()
    _cache[key] = (now, value)
    return value


def _today() -> str:
    return datetime.now(TIMEZONE).date().isoformat()


def _clean(row: dict, allowed: list[str]) -> dict:
    out = {k: row.get(k) for k in allowed if k in row}
    assert_safe({k.lower(): None for k in row})  # reject private column names, even if not selected
    assert_safe(out)
    return out


def _read(table: str, limit: int, filters: dict | None = None, order: str | None = None):
    if table not in TABLES:
        raise ValueError("Table not allowed")
    cols = TABLES[table]
    params = {"select": ",".join(cols), "limit": str(max(1, min(limit, MAX_ROWS)))}
    if filters:
        params.update(filters)  # filters are built by our code; user text is only a validated value
    if order:
        params["order"] = order
    rows = _client.get(f"/{table}", params).json()
    return [_clean(r, cols) for r in rows]


# ------------------------------- public API -------------------------------

def get_upcoming_events(limit: int, event_type: str | None):
    def run():
        today = _today()
        filters = {"or": f"(event_date.gte.{today},event_end_date.gte.{today})"}
        if event_type:
            filters["event_type"] = f"ilike.{event_type}"
        return _read("events", limit, filters, order="event_date.asc")
    return _cached(("upcoming", limit, event_type, _today()), run)


def get_past_events(limit: int, event_type: str | None):
    def run():
        today = _today()
        filters = {
            "event_date": f"lt.{today}",
            "or": f"(event_end_date.is.null,event_end_date.lt.{today})",
        }
        if event_type:
            filters["event_type"] = f"ilike.{event_type}"
        return _read("events", limit, filters, order="event_date.desc")
    return _cached(("past", limit, event_type, _today()), run)


def search_events(query: str, limit: int):
    def run():
        return _read("events", limit, {"name": f"ilike.*{query}*"}, order="event_date.desc")
    return _cached(("search", query.lower(), limit), run)


def get_event(event_id: str):
    def run():
        rows = _read("events", 1, {"id": f"eq.{event_id}"})
        return rows[0] if rows else None
    return _cached(("event", event_id), run)


def get_site_value(key: str):
    """Value of ONE site_config key, or None. Only keys in PUBLIC_SITE_CONFIG_KEYS are readable."""
    if key not in PUBLIC_SITE_CONFIG_KEYS:
        return None

    def run():
        rows = _read("site_config", 1, {"key": f"eq.{key}"})
        if not rows:
            return None
        value = rows[0]["value"]
        if isinstance(value, str):  # column stored as text: parse the JSON held in the cell
            try:
                value = json.loads(value)
            except ValueError:
                pass
        return value
    return _cached(("site", key), run)


def get_company() -> dict | None:
    return get_site_value("company_info")


def get_services() -> list | None:
    return get_site_value("services")


def search_faq(query: str) -> list | None:
    faqs = get_site_value("faq")
    if faqs is None:
        return None
    q = query.lower()
    hits = []
    for f in faqs:
        keywords = " ".join(f.get("keywords", []) if isinstance(f.get("keywords"), list) else [])
        if q in f.get("question", "").lower() or q in f.get("answer", "").lower() or q in keywords.lower():
            hits.append({k: f.get(k) for k in ("id", "question", "answer", "category")})
    return hits[:10]


def count_attendance(event_id: str) -> int:
    """Returns only a number. Attendee rows are never returned."""
    def run():
        resp = _client.get(
            "/attendance",
            {"select": "id", "event_id": f"eq.{event_id}", "limit": "1"},
            headers={"Prefer": "count=exact"},
        )
        return int(resp.headers["content-range"].split("/")[-1])
    return _cached(("attendance", event_id), run)


def verify_certificate(certificate_id: str):
    def run():
        rows = _read("certificates", 1, {"certificate_id": f"eq.{certificate_id}"})
        return rows[0] if rows else None
    return _cached(("cert", certificate_id), run)
