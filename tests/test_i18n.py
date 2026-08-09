"""Unit tests for i18n helpers."""
import pytest

from app.modules.api_bridge.engine.i18n import flatten_i18n, flatten_row, merge_i18n


def test_flatten_i18n_exact_match():
    assert flatten_i18n({"en": "Hello", "es": "Hola"}, "es") == "Hola"


def test_flatten_i18n_fallback():
    assert flatten_i18n({"en": "Hello"}, "es", "en") == "Hello"


def test_flatten_i18n_double_fallback():
    assert flatten_i18n({"en": "Hello"}, "fr", "es") == "Hello"


def test_flatten_i18n_empty_string_falls_through():
    # Empty string is falsy so falls to fallback
    assert flatten_i18n({"en": "English", "es": ""}, "es", "en") == "English"


def test_flatten_i18n_not_a_dict():
    # Pass-through for non-dict values (e.g. plain text column)
    assert flatten_i18n("plain", "es") == "plain"
    assert flatten_i18n(None, "es") is None
    assert flatten_i18n(42, "es") == 42


def test_flatten_row_selectively():
    row = {"id": 1, "name": {"en": "Widget", "es": "Artefacto"}, "price": 9.99}
    out = flatten_row(row, {"name"}, "es", "en")
    assert out["id"] == 1
    assert out["name"] == "Artefacto"
    assert out["price"] == 9.99


def test_flatten_row_no_i18n_fields():
    row = {"id": 1, "code": "WIDGET"}
    out = flatten_row(row, set(), "es", "en")
    assert out == row


def test_merge_i18n_adds_new_locale():
    existing = {"en": "Widget"}
    result   = merge_i18n(existing, {"es": "Artefacto"})
    assert result == {"en": "Widget", "es": "Artefacto"}
    # original is not mutated
    assert "es" not in existing


def test_merge_i18n_overwrites_existing():
    existing = {"en": "Widget", "es": "Old"}
    result   = merge_i18n(existing, {"es": "Nuevo"})
    assert result["es"] == "Nuevo"
    assert result["en"] == "Widget"


def test_merge_i18n_none_existing():
    result = merge_i18n(None, {"en": "Hello"})
    assert result == {"en": "Hello"}
