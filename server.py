
"""
MCP server for AWS Tulas website.
Read-only tools, Descope authentication, scope enforcement.

Run:
    python server.py

MCP endpoint:
    http://localhost:8000/mcp

OAuth metadata:
    http://localhost:8000/.well-known/oauth-protected-resource
"""

import functools
import logging
import os

from dotenv import load_dotenv

load_dotenv()

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

import auth
import data_layer as data

from security import (
    InputRejected,
    check_certificate_id,
    check_text,
    check_uuid,
    clamp_limit,
)


# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------

logging.basicConfig(level=logging.INFO)

log = logging.getLogger("mcp.server")

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8080"))

SERVER_URL = os.environ.get(
    "MCP_SERVER_URL",
    "https://awssbgmcp-production-0c762792.up.railway.app/mcp"
).rstrip("/")

DESCOPE_CONFIG_URL = os.environ.get(
    "DESCOPE_CONFIG_URL", ""
).strip().rstrip("/")

UNAVAILABLE = {
    "error": "Data temporarily unavailable. Please try again later."
}


# --------------------------------------------------
# MCP SERVER
# --------------------------------------------------

mcp = FastMCP(
    "MyWebsite",
    auth=auth.build_provider()
)


# --------------------------------------------------
# OAUTH PROTECTED RESOURCE METADATA
# --------------------------------------------------

@mcp.custom_route(
    "/.well-known/oauth-protected-resource",
    methods=["GET"]
)
async def protected_resource_metadata(request: Request):

    if not DESCOPE_CONFIG_URL:
        log.error("DESCOPE_CONFIG_URL is not configured.")

        return JSONResponse(
            {"error": "OAuth issuer configuration is missing."},
            status_code=503
        )

    required_scope = auth.REQUIRED_SCOPE

    scopes = (
        [required_scope]
        if isinstance(required_scope, str)
        else list(required_scope)
    )

    metadata = {
        "resource": SERVER_URL,
        "authorization_servers": [
            DESCOPE_CONFIG_URL
        ],
        "bearer_methods_supported": [
            "header"
        ],
        "scopes_supported": scopes
    }

    return JSONResponse(metadata)


# --------------------------------------------------
# HELPERS
# --------------------------------------------------

def _flag(name: str) -> bool:
    return (
        os.environ.get(name, "false")
        .strip()
        .lower() in {"1", "true", "yes"}
    )


def safe(fn):
    """
    Convert unexpected failures into safe messages.
    Detailed errors remain in server logs.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):

        try:
            return fn(*args, **kwargs)

        except InputRejected as e:
            return {"error": str(e)}

        except Exception:
            log.exception(
                "Tool failed: %s",
                fn.__name__
            )
            return dict(UNAVAILABLE)

    return wrapper


def secure_tool(fn=None, *, scopes=None):
    """
    Register tools with mandatory authentication.

    scopes=None:
        Requires REQUIRED_SCOPE.

    scopes=():
        Requires a valid token but no additional scope.
    """

    def register(f):

        required = (
            (auth.REQUIRED_SCOPE,)
            if scopes is None
            else tuple(scopes)
        )

        return mcp.tool()(
            auth.protected(
                safe(f),
                *required
            )
        )

    return register(fn) if fn else register


def _missing(what: str) -> dict:
    return {
        "message": f"{what} has not been published yet."
    }


# --------------------------------------------------
# COMPANY INFORMATION
# --------------------------------------------------

@secure_tool
def get_company_info() -> dict:
    """
    Get general company information:
    name, about, mission, location and links.
    """

    info = data.get_company()

    return (
        {"company": info}
        if info
        else _missing("Company information")
    )


# --------------------------------------------------
# SERVICES
# --------------------------------------------------

@secure_tool
def list_services() -> dict:
    """
    List available services, their scope
    and what each service includes.
    """

    services = data.get_services()

    return (
        {
            "count": len(services),
            "services": services
        }
        if services
        else _missing("Services")
    )


# --------------------------------------------------
# FAQ
# --------------------------------------------------

@secure_tool
def search_faq(query: str) -> dict:
    """
    Search frequently asked questions by keyword.
    """

    q = check_text(query, 60)

    if not q:
        raise InputRejected(
            "Please provide a search phrase."
        )

    hits = data.search_faq(q)

    if hits is None:
        return _missing("The FAQ")

    return {
        "count": len(hits),
        "faqs": hits
    }


# --------------------------------------------------
# UPCOMING EVENTS
# --------------------------------------------------

@secure_tool
def list_upcoming_events(
    limit: int = 10,
    event_type: str | None = None
) -> dict:
    """
    List upcoming and ongoing public events.
    """

    rows = data.get_upcoming_events(
        clamp_limit(limit),
        check_text(event_type, 50)
    )

    return {
        "count": len(rows),
        "events": rows
    }


# --------------------------------------------------
# PAST EVENTS
# --------------------------------------------------

@secure_tool
def list_past_events(
    limit: int = 10,
    event_type: str | None = None
) -> dict:
    """
    List past public events, most recent first.
    """

    rows = data.get_past_events(
        clamp_limit(limit),
        check_text(event_type, 50)
    )

    return {
        "count": len(rows),
        "events": rows
    }


# --------------------------------------------------
# EVENT SEARCH
# --------------------------------------------------

@secure_tool
def search_events(
    query: str,
    limit: int = 10
) -> dict:
    """
    Search events by name or topic.
    """

    q = check_text(query, 60)

    if not q:
        raise InputRejected(
            "Please provide a search phrase."
        )

    rows = data.search_events(
        q,
        clamp_limit(limit)
    )

    return {
        "count": len(rows),
        "events": rows
    }


# --------------------------------------------------
# EVENT DETAILS
# --------------------------------------------------

@secure_tool
def get_event_details(event_id: str) -> dict:
    """
    Get full public details of one event.
    """

    event = data.get_event(
        check_uuid(event_id)
    )

    return (
        {"event": event}
        if event
        else {
            "error": "No event found with that id."
        }
    )


# --------------------------------------------------
# OPTIONAL ATTENDANCE TOOL
# --------------------------------------------------

if _flag("ENABLE_ATTENDANCE_COUNTS"):

    @secure_tool
    def get_event_attendance_count(
        event_id: str
    ) -> dict:
        """
        Get the number of attendees.
        Never returns attendee names.
        """

        eid = check_uuid(event_id)

        return {
            "event_id": eid,
            "attendance_count": data.count_attendance(eid)
        }


# --------------------------------------------------
# OPTIONAL CERTIFICATE VERIFICATION
# --------------------------------------------------

if _flag("ENABLE_CERTIFICATE_VERIFY"):

    @secure_tool
    def verify_certificate(
        certificate_id: str
    ) -> dict:
        """
        Verify a certificate using its exact ID.
        """

        cert = data.verify_certificate(
            check_certificate_id(certificate_id)
        )

        if not cert:
            return {
                "valid": False,
                "message": "No certificate found with that id."
            }

        return {
            "valid": True,
            "certificate": cert
        }


# --------------------------------------------------
# OPTIONAL DEBUG TOOL
# --------------------------------------------------

if _flag("ENABLE_WHOAMI"):

    @secure_tool(scopes=())
    def whoami() -> dict:
        """
        Return the authenticated caller's claims.
        Keep disabled in production.
        """

        return auth.caller_claims()


# --------------------------------------------------
# START SERVER
# --------------------------------------------------

if __name__ == "__main__":

    log.info("Starting MyWebsite MCP server...")
    log.info("MCP endpoint: %s", SERVER_URL)

    mcp.run(
        transport="http",
        host=HOST,
        port=PORT,
        stateless_http=True,
        json_response=True
    )
