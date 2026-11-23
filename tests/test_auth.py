"""Tests auth.require_access() directly. Run: python tests/test_auth.py"""
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DESCOPE_CONFIG_URL"] = "https://api.descope.com/v1/apps/agentic/PTESTPROJECT/MTESTSERVER/.well-known/openid-configuration"
import auth  # noqa: E402
from descope_mcp import InsufficientScopeError  # noqa: E402


def tok(*scopes):
    return SimpleNamespace(scopes=list(scopes), claims={}, client_id="c", expires_at=1)


# scope present -> passes
with patch.object(auth, "get_access_token", return_value=tok("data:read", "x")):
    auth.require_access("data:read")
    auth.require_access()  # no scope required: a valid token is enough

# scope missing -> InsufficientScopeError
for scopes in ([], ["other"]):
    with patch.object(auth, "get_access_token", return_value=tok(*scopes)):
        try:
            auth.require_access("data:read")
        except InsufficientScopeError:
            pass
        else:
            raise AssertionError(f"scopes={scopes} should be rejected")

# no token -> PermissionError, for both a scope check and a token-only check
with patch.object(auth, "get_access_token", return_value=None):
    for args in (("data:read",), ()):
        try:
            auth.require_access(*args)
        except PermissionError:
            pass
        else:
            raise AssertionError("missing token should be rejected")

# base_url: configurable, /mcp stripped
with patch.dict(os.environ, {"BASE_URL": "https://mcp.example.com/mcp/"}):
    assert auth.base_url() == "https://mcp.example.com"
print("ALL AUTH TESTS PASSED")
