"""Call the running server from the terminal. No secrets are stored in this file.

    python test_client.py --oauth                      # opens the browser for Descope login + consent
    python test_client.py --oauth --url https://your-server.example.com/mcp
    python test_client.py                              # no login: the server should answer 401

The server only accepts requests with a valid Descope token carrying the data:read scope,
so --oauth is needed for everything except checking that anonymous access is refused.
"""

import asyncio
import sys

from fastmcp import Client

url = "http://localhost:8000/mcp"
if "--url" in sys.argv:
    url = sys.argv[sys.argv.index("--url") + 1]
auth = "oauth" if "--oauth" in sys.argv else None


async def call(client: Client, name: str, args: dict | None = None):
    """Call a tool and return plain Python data."""
    r = await client.call_tool(name, args or {}, raise_on_error=False)
    return r.structured_content if r.structured_content is not None else r.content[0].text


def show(label: str, value) -> None:
    text = str(value)
    print(f"{label}: {text[:300]}{'...' if len(text) > 300 else ''}")


async def main() -> None:
    async with Client(url, auth=auth) as client:
        tools = [t.name for t in await client.list_tools()]
        print("tools:", tools)

        if "whoami" in tools:  # only present when ENABLE_WHOAMI=true on the server
            show("whoami", await call(client, "whoami"))

        show("company", await call(client, "get_company_info"))
        show("services", await call(client, "list_services"))
        show("faq 'join'", await call(client, "search_faq", {"query": "join"}))

        events = await call(client, "list_upcoming_events", {"limit": 3})
        show("upcoming events", events)
        first = (events.get("events") or [{}])[0].get("id") if isinstance(events, dict) else None
        if first:
            show("event details", await call(client, "get_event_details", {"event_id": first}))

        show("past events", await call(client, "list_past_events", {"limit": 3}))
        show("search 'workshop'", await call(client, "search_events", {"query": "workshop"}))

        # input checks: this must be refused
        show("sql attempt", await call(client, "search_events", {"query": "x; DROP TABLE events"}))


try:
    asyncio.run(main())
except Exception as e:  # e.g. 401 when running without --oauth
    print(f"Request refused or failed: {type(e).__name__}: {e}")
    sys.exit(1)
