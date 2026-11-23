"""Offline tests: the database and Descope are mocked. Run: python tests/test_offline.py"""
import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.update(
    SUPABASE_URL="https://example.supabase.co", SUPABASE_KEY="test",
    PUBLIC_SITE_CONFIG_KEYS="company_info,services,faq",
    ENABLE_ATTENDANCE_COUNTS="true", ENABLE_CERTIFICATE_VERIFY="true", ENABLE_WHOAMI="true",
    DESCOPE_CONFIG_URL="https://api.descope.com/v1/apps/agentic/PTESTPROJECT/MTESTSERVER/.well-known/openid-configuration",
)
import auth  # noqa: E402
import data_layer  # noqa: E402
import server  # noqa: E402

UUID = "11111111-1111-1111-1111-111111111111"
GENERIC = {"error": "Data temporarily unavailable. Please try again later."}
calls = []


class FakeResp:
    def __init__(self, rows, headers=None):
        self._r, self.headers = rows, headers or {}

    def json(self):
        return self._r


SITE = {
    "company_info": {"name": "Club", "mission": "m", "contact": {"website_contact_page": "https://x.example/contact"}},
    "services": [{"id": "s1", "name": "Workshop"}],
    "faq": [{"id": "f1", "question": "How do I join?", "answer": "Sign up.", "category": "membership",
             "keywords": ["join"], "source": "internal", "confidence": "high"}],
}


class FakeClient:
    def get(self, path, params, headers=None):
        calls.append((path, params))
        if path == "/events":
            return FakeResp([{"id": UUID, "name": "Workshop", "description": "d", "event_date": "2030-01-01",
                              "event_end_date": None, "event_timing": "10am", "speaker_name": "A",
                              "event_type": "workshop", "attendance_open": True}])  # extra column must be dropped
        if path == "/attendance":
            return FakeResp([{"id": "x"}], {"content-range": "0-0/57"})
        if path == "/site_config":
            key = params["key"].removeprefix("eq.")
            return FakeResp([{"key": key, "value": SITE[key]}] if key in SITE else [])
        if path == "/certificates":
            return FakeResp([])
        raise AssertionError(f"unexpected table {path}")


def token(*scopes):
    return SimpleNamespace(scopes=list(scopes), claims={"sub": "user-1"}, client_id="c1", expires_at=123)


async def call(name, args=None, tok=None):
    with patch.object(auth, "get_access_token", return_value=tok or token("data:read")):
        res = await server.mcp.call_tool(name, args or {})
    return res.structured_content


async def main():
    data_layer._client = FakeClient()
    names = sorted(t.name for t in await server.mcp.list_tools())
    print("tools:", names)
    assert not any(w in n for n in names for w in ("sql", "query", "table", "url")), names
    for t in await server.mcp.list_tools():
        for p in t.parameters.get("properties", {}):
            assert p not in {"sql", "table", "column", "url", "query_string", "select"}, (t.name, p)

    # 1. only allowed fields are returned, SELECT never uses *
    r = await call("list_upcoming_events", {"limit": 5})
    assert r["events"][0]["name"] == "Workshop" and "attendance_open" not in r["events"][0]
    sel = calls[-1][1]["select"]
    assert "*" not in sel and "email" not in sel and "roll" not in sel, sel
    assert int(calls[-1][1]["limit"]) <= 20
    r = await call("list_upcoming_events", {"limit": 999})
    assert int(calls[-1][1]["limit"]) == 20
    await call("list_past_events", {})
    assert (await call("get_event_details", {"event_id": UUID}))["event"]["id"] == UUID

    # 2. site_config tools read only allowlisted keys, with allowlisted output
    assert (await call("get_company_info"))["company"]["name"] == "Club"
    assert (await call("list_services"))["count"] == 1
    r = await call("search_faq", {"query": "join"})
    assert r["count"] == 1 and set(r["faqs"][0]) == {"id", "question", "answer", "category"}
    assert data_layer.get_site_value("admin_secret") is None  # not in PUBLIC_SITE_CONFIG_KEYS
    assert not any(c[0] == "/site_config" and "admin_secret" in str(c[1]) for c in calls)

    # 3. bad input is rejected, and SQL-looking input gets the exact message
    n = len(calls)
    for bad in ["x; DROP TABLE events", "a UNION SELECT * FROM participants", "name') OR 1=1 --", "a*b", "a,b"]:
        for tool in ("search_events", "search_faq"):
            r = await call(tool, {"query": bad})
            assert "error" in r and "events" not in r and "faqs" not in r, (tool, bad)
    assert (await call("search_events", {"query": "x; DROP TABLE events"})) == {"error": "SQL commands are not allowed."}
    assert "error" in await call("list_upcoming_events", {"event_type": "a'; delete from events"})
    assert "error" in await call("get_event_details", {"event_id": "not-a-uuid"})
    assert "error" in await call("verify_certificate", {"certificate_id": "x' or 1=1"})
    assert len(calls) == n, "rejected input must never reach the database"
    assert (await call("get_event_attendance_count", {"event_id": UUID}))["attendance_count"] == 57
    assert (await call("verify_certificate", {"certificate_id": "ABC-123"}))["valid"] is False

    # 4. a result containing "email" is blocked, at any depth, and users get a generic error
    class Leaky(FakeClient):
        def get(self, path, params, headers=None):
            return FakeResp([{"id": "1", "name": "n", "email": "a@b.c"}])
    data_layer._client = Leaky()
    data_layer._cache.clear()
    assert await call("list_upcoming_events") == GENERIC

    SITE["company_info"]["contact"]["public_email"] = "someone@example.com"  # nested key
    data_layer._client = FakeClient()
    data_layer._cache.clear()
    assert await call("get_company_info") == GENERIC
    del SITE["company_info"]["contact"]["public_email"]
    SITE["company_info"]["about"] = "write to someone@example.com"  # email inside a text value
    data_layer._cache.clear()
    assert await call("get_company_info") == GENERIC

    # 5. database failure -> generic error only
    class Down(FakeClient):
        def get(self, path, params, headers=None):
            raise RuntimeError("connection refused: https://secret-host")
    data_layer._client = Down()
    data_layer._cache.clear()
    assert await call("list_past_events") == GENERIC

    # 6. EVERY tool refuses a token without data:read (and no token), except whoami (token only)
    data_layer._client = FakeClient()
    data_layer._cache.clear()
    sample = {"query": "join", "event_id": UUID, "certificate_id": "ABC-123"}
    n = len(calls)
    for t in await server.mcp.list_tools():
        args = {k: v for k, v in sample.items() if k in t.parameters.get("properties", {})}
        if t.name == "whoami":
            continue
        r = await call(t.name, args, tok=token("other:scope"))
        assert "error" in r and "events" not in r and "company" not in r, (t.name, r)
        with patch.object(auth, "get_access_token", return_value=None):
            r = (await server.mcp.call_tool(t.name, args)).structured_content
        assert r == {"error": "Authentication required"}, (t.name, r)
    assert len(calls) == n, "unauthorised calls must never reach the database"

    # 7. whoami: valid token without data:read works, returns only claims
    r = await call("whoami", tok=token("other:scope"))
    assert r == {"user_id": "user-1", "client_id": "c1", "scopes": ["other:scope"], "expires_at": 123}
    with patch.object(auth, "get_access_token", return_value=None):
        assert (await server.mcp.call_tool("whoami", {})).structured_content == {"error": "Authentication required"}

    # 8. no write code in the data layer
    src = open(os.path.join(os.path.dirname(data_layer.__file__), "data_layer.py"), encoding="utf-8").read()
    for word in (".post(", ".patch(", ".put(", ".delete(", "open(", "read_text"):
        assert word not in src, word
    assert not hasattr(data_layer._client, "post")
    print("ALL OFFLINE TESTS PASSED")


asyncio.run(main())
