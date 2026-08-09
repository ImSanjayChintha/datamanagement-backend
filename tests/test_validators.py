"""Unit tests for field-level insert/update validation."""
import pytest

from app.modules.api_bridge.engine.errors import EngineError
from app.modules.api_bridge.engine.validators import validate_insert_data, validate_update_data
from tests.conftest import make_field, make_object


# ── Insert validation ─────────────────────────────────────────────────────────

def test_insert_valid_data():
    obj  = make_object("countries", [
        make_field("code",  "text", is_required=True),
        make_field("label", "text"),
    ])
    out  = validate_insert_data(obj, {"code": "BR", "label": "Brazil"})
    assert out == {"code": "BR", "label": "Brazil"}


def test_insert_missing_required():
    obj = make_object("countries", [
        make_field("code", "text", is_required=True),
    ])
    with pytest.raises(EngineError) as exc:
        validate_insert_data(obj, {})
    assert exc.value.code == "validation_failed"
    assert "code" in exc.value.errors


def test_insert_unknown_field():
    obj = make_object("countries", [make_field("code", "text")])
    with pytest.raises(EngineError) as exc:
        validate_insert_data(obj, {"code": "BR", "hacker_field": "evil"})
    assert "hacker_field" in exc.value.errors


def test_insert_readonly_rejected():
    obj = make_object("countries", [
        make_field("code",        "text"),
        make_field("inserted_at", "timestamp", is_readonly=True),
    ])
    with pytest.raises(EngineError) as exc:
        validate_insert_data(obj, {"code": "BR", "inserted_at": "2024-01-01"})
    assert "inserted_at" in exc.value.errors


def test_insert_merge_suffix_rejected():
    obj = make_object("countries", [make_field("name", "i18n_text")])
    with pytest.raises(EngineError) as exc:
        validate_insert_data(obj, {"name~merge": {"en": "Brazil"}})
    assert "name~merge" in exc.value.errors


def test_insert_type_error_number():
    obj = make_object("products", [make_field("price", "number")])
    with pytest.raises(EngineError) as exc:
        validate_insert_data(obj, {"price": "not-a-number"})
    assert "price" in exc.value.errors
    assert exc.value.errors["price"]["code"] == "type_error"


def test_insert_max_length():
    obj = make_object("products", [
        make_field("code", "text", validation={"max_length": 5}),
    ])
    with pytest.raises(EngineError) as exc:
        validate_insert_data(obj, {"code": "TOOLONG"})
    assert exc.value.errors["code"]["code"] == "max_length"


def test_insert_select_invalid_option():
    obj = make_object("products", [
        make_field("status", "select", options=[{"value": "active"}, {"value": "inactive"}]),
    ])
    with pytest.raises(EngineError) as exc:
        validate_insert_data(obj, {"status": "deleted"})
    assert exc.value.errors["status"]["code"] == "invalid_option"


def test_insert_select_valid_option():
    obj = make_object("products", [
        make_field("status", "select", options=[{"value": "active"}, {"value": "inactive"}]),
    ])
    out = validate_insert_data(obj, {"status": "active"})
    assert out["status"] == "active"


def test_insert_i18n_must_be_dict():
    obj = make_object("products", [make_field("name", "i18n_text")])
    with pytest.raises(EngineError) as exc:
        validate_insert_data(obj, {"name": "plain string"})
    assert exc.value.errors["name"]["code"] == "type_error"


def test_insert_i18n_valid():
    obj = make_object("products", [make_field("name", "i18n_text")])
    out = validate_insert_data(obj, {"name": {"en": "Widget", "es": "Artefacto"}})
    assert out["name"] == {"en": "Widget", "es": "Artefacto"}


# ── Update validation ─────────────────────────────────────────────────────────

def test_update_partial_allowed():
    obj = make_object("countries", [
        make_field("code",  "text", editable_on_update=False),
        make_field("label", "text"),
    ])
    out = validate_update_data(obj, {"label": "Brasil"})
    assert out == {"label": "Brasil"}


def test_update_not_editable_on_update():
    obj = make_object("countries", [
        make_field("code", "text", editable_on_update=False),
    ])
    with pytest.raises(EngineError) as exc:
        validate_update_data(obj, {"code": "XX"})
    assert exc.value.errors["code"]["code"] == "not_editable_on_update"


def test_update_merge_suffix_preserved():
    obj = make_object("products", [
        make_field("name", "i18n_text", editable_on_update=True),
    ])
    out = validate_update_data(obj, {"name~merge": {"es": "Español"}})
    assert "name~merge" in out
    assert out["name~merge"] == {"es": "Español"}


def test_update_explicit_null_allowed():
    obj = make_object("products", [make_field("description", "text")])
    out = validate_update_data(obj, {"description": None})
    assert out["description"] is None
