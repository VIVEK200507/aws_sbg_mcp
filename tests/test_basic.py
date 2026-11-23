"""Offline tests: no real database is contacted. Run: python tests/test_basic.py"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.update(SUPABASE_URL="https://example.supabase.co", SUPABASE_KEY="test",
                  PUBLIC_SITE_CONFIG_KEYS="about", ENABLE_ATTENDANCE_COUNTS="true",
                  ENABLE_CERTIFICATE_VERIFY="true")
import data_layer, server  # noqa: E402

calls = []
class FakeResp:
    def __init__(self, rows, headers=None): self._r, self.headers = rows, headers or {}
    def json(self): return self._r
class FakeClient:
    def get(self, path, params, headers=None):
        calls.append((path, params))
        if path == "/events":
            return FakeResp([{"id": "11111111-1111-1111-1111-111111111111", "name": "Workshop",
                              "description": "d", "event_date": "2030-01-01", "event_end_date": None,
                              "event_timing": "10am", "speaker_name": "A", "event_type": "workshop",
                              "attendance_open": True}])
        if path == "/attendance": return FakeResp([{"id": "x"}], {"content-range": "0-0/57"})
        if path == "/site_config": return FakeResp([{"key": "about", "value": {"t": "hi"}}])
        if path == "/certificates": return FakeResp([])
        raise AssertionError(path)
data_layer._client = FakeClient()

async def main():
    names = sorted(t.name for t in await server.mcp.list_tools())
    print("tools:", names)
    assert "get_event_attendance_count" in names and "verify_certificate" in names
    assert not any("sql" in n or "query" in n for n in names)

    async def call(name, args):
        import json
        res = await server.mcp.call_tool(name, args)
        if isinstance(res, tuple):
            res = res[0]
        return json.loads(res[0].text)

    r = await call("list_upcoming_events", {"limit": 5})
    assert r["events"][0]["name"] == "Workshop" and "attendance_open" not in r["events"][0]
    sel = calls[-1][1]["select"]; assert "email" not in sel and "roll" not in sel and "*" not in sel

    for bad in ["x; DROP TABLE events", "a UNION SELECT * FROM participants", "name') OR 1=1 --", "a*b", "a,b"]:
        r = await call("search_events", {"query": bad}); assert "error" in r, bad
    r = await call("search_events", {"query": "Create your resume workshop"}); assert "events" in r
    r = await call("get_event_details", {"event_id": "not-a-uuid"}); assert "error" in r
    r = await call("get_event_attendance_count", {"event_id": "11111111-1111-1111-1111-111111111111"})
    assert r["attendance_count"] == 57
    r = await call("verify_certificate", {"certificate_id": "ABC-123"}); assert r["valid"] is False
    r = await call("verify_certificate", {"certificate_id": "x' or 1=1"}); assert "error" in r
    r = await call("get_website_info", {}); assert "info" in r
    # leak guard
    class Leaky(FakeClient):
        def get(self, path, params, headers=None): return FakeResp([{"id": "1", "name": "n", "email": "a@b.c"}])
    data_layer._client = Leaky(); data_layer._cache.clear()
    r = await call("list_upcoming_events", {}); assert r == {"error": "Data temporarily unavailable. Please try again later."}
    print("ALL TESTS PASSED")
asyncio.run(main())
