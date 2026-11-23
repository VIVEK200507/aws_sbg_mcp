"""Descope authentication for the MCP server (imported by server.py, not run on its own).

How it protects the server:
  1. build_provider() returns a DescopeProvider. FastMCP uses it to reject every HTTP request
     that has no valid Descope access token (signature, expiry and audience are checked) and to
     tell MCP clients where to log in (the 401 + .well-known discovery flow).
  2. require_access(*scopes) re-reads the verified token inside a tool and enforces scopes.
  3. protected(fn, *scopes) is the decorator that server.py applies to EVERY tool, so a tool
     cannot run without the scope check.

Descope console: add the scope in REQUIRED_SCOPE (default `data:read`) to your MCP server's
scopes, otherwise every tool call is rejected with an insufficient-scope error.
"""

import functools
import os

from descope_mcp import InsufficientScopeError, require_scopes
from dotenv import load_dotenv
from fastmcp.server.auth.providers.descope import DescopeProvider
from fastmcp.server.dependencies import get_access_token

load_dotenv()

REQUIRED_SCOPE = os.environ.get("MCP_REQUIRED_SCOPE", "data:read")


def base_url() -> str:
    """Public base URL of this server WITHOUT the /mcp path (set BASE_URL in production).

    Falls back to MCP_SERVER_URL (the old variable, with /mcp), then to localhost.
    """
    url = os.environ.get("BASE_URL") or os.environ.get("MCP_SERVER_URL")
    if not url:
        url = f"http://localhost:{os.environ.get('PORT', '8000')}"
    return url.rstrip("/").removesuffix("/mcp")


def build_provider() -> DescopeProvider:
    return DescopeProvider(
        config_url=os.environ["DESCOPE_CONFIG_URL"],
        base_url=base_url(),
        scopes_supported=[REQUIRED_SCOPE],  # advertised to clients during discovery
    )


def require_access(*scopes: str) -> None:
    """Raise unless the caller has a valid token that includes every scope in `scopes`.

    PermissionError         -> no token at all
    InsufficientScopeError  -> token present but a required scope is missing
    With no scopes it only requires a valid token.
    """
    tok = get_access_token()  # already signature-checked by DescopeProvider
    if tok is None:
        raise PermissionError("Authentication required")
    require_scopes({"scopes": list(tok.scopes or [])}, list(scopes))


def protected(fn, *scopes: str):
    """Decorator: run the scope check BEFORE the tool body. Failures return a plain error object."""
    required = scopes

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            require_access(*required)
        except InsufficientScopeError as e:
            return e.to_dict()
        except PermissionError:
            return {"error": "Authentication required"}
        return fn(*args, **kwargs)

    wrapper.requires_scopes = required
    return wrapper


def caller_claims() -> dict:
    """The caller's own claims only (never the raw token)."""
    tok = get_access_token()
    return {
        "user_id": tok.claims.get("sub") if tok and tok.claims else None,
        "client_id": tok.client_id if tok else None,
        "scopes": list(tok.scopes or []) if tok else [],
        "expires_at": tok.expires_at if tok else None,
    }
