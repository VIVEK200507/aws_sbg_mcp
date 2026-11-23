"""MCP server for the website: read-only, fixed tools, no SQL accepted, Descope-protected.

Run:  python server.py      ->  http://localhost:8000/mcp  (streamable HTTP)
Every tool is registered through secure_tool(), which applies the auth.py scope check.
"""
import functools
import logging
import os

from dotenv import load_dotenv

load_dotenv()

from fastmcp import FastMCP  # noqa: E402

import auth  # noqa: E402
import data_layer as data  # noqa: E402  (needs env loaded first)
from security import (  # noqa: E402
    InputRejected, check_certificate_id, check_text, check_uuid, clamp_limit,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("mcp.server")

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))
UNAVAILABLE = {"error": "Data temporarily unavailable. Please try again later."}

mcp = FastMCP("MyWebsite", auth=auth.build_provider())


def _flag(name: str) -> bool:
    return os.environ.get(name, "false").strip().lower() in {"1", "true", "yes"}


def safe(fn):
    """Turn any failure into a short, safe message (details go to the server log only)."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except InputRejected as e:
            return {"error": str(e)}
        except Exception:
            log.exception("Tool failed: %s", fn.__name__)
            return dict(UNAVAILABLE)
    return wrapper


def secure_tool(fn=None, *, scopes=None):
    """The ONLY way tools are registered. Auth check runs first, then errors are sanitised.

    scopes=None -> the default REQUIRED_SCOPE (data:read). scopes=() -> valid token only.
    """
    def register(f):
        required = (auth.REQUIRED_SCOPE,) if scopes is None else tuple(scopes)
        return mcp.tool()(auth.protected(safe(f), *required))
    return register(fn) if fn else register


def _missing(what: str) -> dict:
    return {"message": f"{what} has not been published yet."}


@secure_tool
def get_company_info() -> dict:
    """Get general information about the company: name, about, mission, location and links."""
    info = data.get_company()
    return {"company": info} if info else _missing("Company information")


@secure_tool
def list_services() -> dict:
    """List the services we offer, including the scope of each service and what it includes."""
    services = data.get_services()
    return {"count": len(services), "services": services} if services else _missing("Services")


@secure_tool
def search_faq(query: str) -> dict:
    """Search frequently asked questions by keyword."""
    q = check_text(query, 60)
    if not q:
        raise InputRejected("Please provide a search phrase.")
    hits = data.search_faq(q)
    if hits is None:
        return _missing("The FAQ")
    return {"count": len(hits), "faqs": hits}


@secure_tool
def list_upcoming_events(limit: int = 10, event_type: str | None = None) -> dict:
    """List upcoming and ongoing public events, soonest first. Returns id, name,
    description, dates, timing, speaker and event type. Optionally filter by event_type."""
    rows = data.get_upcoming_events(clamp_limit(limit), check_text(event_type, 50))
    return {"count": len(rows), "events": rows}


@secure_tool
def list_past_events(limit: int = 10, event_type: str | None = None) -> dict:
    """List past public events, most recent first. Optionally filter by event_type."""
    rows = data.get_past_events(clamp_limit(limit), check_text(event_type, 50))
    return {"count": len(rows), "events": rows}


@secure_tool
def search_events(query: str, limit: int = 10) -> dict:
    """Search events by name (case-insensitive, partial match). Use this when the
    user mentions an event by name or topic."""
    q = check_text(query, 60)
    if not q:
        raise InputRejected("Please provide a search phrase.")
    rows = data.search_events(q, clamp_limit(limit))
    return {"count": len(rows), "events": rows}


@secure_tool
def get_event_details(event_id: str) -> dict:
    """Get full public details of one event by its id (the id comes from the event lists)."""
    event = data.get_event(check_uuid(event_id))
    return {"event": event} if event else {"error": "No event found with that id."}


if _flag("ENABLE_ATTENDANCE_COUNTS"):
    @secure_tool
    def get_event_attendance_count(event_id: str) -> dict:
        """Get how many people attended an event. Returns a number only, never names."""
        eid = check_uuid(event_id)
        return {"event_id": eid, "attendance_count": data.count_attendance(eid)}

if _flag("ENABLE_CERTIFICATE_VERIFY"):
    @secure_tool
    def verify_certificate(certificate_id: str) -> dict:
        """Verify a certificate by its exact certificate id. Returns the recipient name,
        event and award if the certificate exists. Cannot search or list certificates."""
        cert = data.verify_certificate(check_certificate_id(certificate_id))
        if not cert:
            return {"valid": False, "message": "No certificate found with that id."}
        return {"valid": True, "certificate": cert}

if _flag("ENABLE_WHOAMI"):  # debugging aid, keep OFF in production
    @secure_tool(scopes=())  # needs a valid token, but not data:read
    def whoami() -> dict:
        """Show the caller's own identity claims (user id, client id, scopes, expiry)."""
        return auth.caller_claims()


if __name__ == "__main__":
    mcp.run(
        transport="http",  # streamable HTTP at /mcp
        host=HOST,
        port=PORT,
        stateless_http=True,
        json_response=True,
    )
