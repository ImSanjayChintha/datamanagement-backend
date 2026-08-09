"""Unit tests for engine guardrails.

Verifies that filter_required, limit_exceeded, expected_mismatch, and
role-check logic fire before any SQL is executed.
"""
import pytest

from app.modules.api_bridge.engine.errors import EngineError
from app.modules.api_bridge.engine.executor import _role_allowed
from app.modules.api_bridge.engine.filters import ParamBag, compile_filter
from tests.conftest import make_field, make_object


# ── Role check ────────────────────────────────────────────────────────────────

def test_wildcard_role_allows_all():
    assert _role_allowed(["reader"], ["*"]) is True


def test_specific_role_allowed():
    assert _role_allowed(["admin"], ["admin", "writer"]) is True


def test_specific_role_denied():
    assert _role_allowed(["reader"], ["admin", "writer"]) is False


def test_empty_user_roles_denied():
    assert _role_allowed([], ["admin"]) is False


# ── limit_exceeded ────────────────────────────────────────────────────────────

def test_limit_exceeded_error():
    from app.modules.api_bridge.engine.errors import limit_exceeded
    err = limit_exceeded(500, 200)
    assert err.code == "limit_exceeded"
    assert err.http_status == 400


# ── filter_required ───────────────────────────────────────────────────────────

def test_filter_required_error():
    from app.modules.api_bridge.engine.errors import filter_required
    err = filter_required()
    assert err.code == "filter_required"
    assert err.http_status == 400


# ── expected_mismatch ─────────────────────────────────────────────────────────

def test_expected_mismatch_error():
    from app.modules.api_bridge.engine.errors import expected_mismatch
    err = expected_mismatch(1, 3)
    assert err.code == "expected_mismatch"
    assert err.http_status == 409
    assert "1" in err.message
    assert "3" in err.message


# ── Leaf count limit ──────────────────────────────────────────────────────────

def test_leaf_count_limit():
    """Filter with more than MAX_LEAF_COUNT conditions should be rejected."""
    from app.modules.api_bridge.engine.filters import MAX_LEAF_COUNT
    f = make_field("code", "text")
    fmap = {"code": f}
    leaves = [{"field": "code", "op": "eq", "value": str(i)} for i in range(MAX_LEAF_COUNT + 1)]
    node   = {"and": leaves}
    params = ParamBag()
    with pytest.raises(EngineError) as exc:
        compile_filter(node, fmap, params)
    assert exc.value.code == "validation_failed"
    assert "conditions" in exc.value.message.lower() or "leaf" in exc.value.message.lower() or "exceed" in exc.value.message.lower()


# ── between requires 2-element list ──────────────────────────────────────────

def test_between_wrong_value():
    f    = make_field("price", "decimal")
    fmap = {"price": f}
    with pytest.raises(EngineError) as exc:
        compile_filter({"field": "price", "op": "between", "value": [10]}, fmap, ParamBag())
    assert exc.value.code == "validation_failed"


# ── EngineError.to_response() ─────────────────────────────────────────────────

def test_engine_error_to_response_shape():
    from app.modules.api_bridge.engine.errors import validation_failed
    err  = validation_failed({"name": {"code": "required", "message": "Name is required"}})
    resp = err.to_response()
    assert resp.status_code == 400
    import json
    body = json.loads(resp.body)
    assert body["ok"] is False
    assert body["error"] == "validation_failed"
    assert "errors" in body
    assert "name" in body["errors"]


def test_engine_error_404():
    from app.modules.api_bridge.engine.errors import unknown_object
    err = unknown_object("pim.bogus")
    assert err.http_status == 404
    assert err.code == "unknown_object"
