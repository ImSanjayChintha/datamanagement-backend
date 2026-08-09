"""Unit tests for the filter DSL compiler.

These tests run without a database — they verify SQL fragment generation
and that user-supplied values NEVER appear in the SQL string (only as $N params).
"""
import pytest

from app.modules.api_bridge.engine.errors import EngineError
from app.modules.api_bridge.engine.filters import ParamBag, compile_filter
from tests.conftest import make_field


# ── Helpers ───────────────────────────────────────────────────────────────────

def fields(*specs):
    """Build a fields_by_name dict: fields('name', ('count','number'))"""
    result = {}
    for s in specs:
        if isinstance(s, str):
            f = make_field(s, "text")
        else:
            name, ft = s
            f = make_field(name, ft)
        result[f.field_name] = f
    return result


def compile(node, field_map, locale=None):
    params = ParamBag()
    sql    = compile_filter(node, field_map, params, locale=locale)
    return sql, params.values


# ── Basic operators ───────────────────────────────────────────────────────────

def test_eq():
    sql, vals = compile({"field": "name", "op": "eq", "value": "Brazil"}, fields("name"))
    assert "t.\"name\" = $1" in sql
    assert vals == ("Brazil",)


def test_neq():
    sql, vals = compile({"field": "name", "op": "neq", "value": "Brazil"}, fields("name"))
    assert "<>" in sql
    assert vals == ("Brazil",)


def test_ilike():
    sql, vals = compile({"field": "name", "op": "ilike", "value": "%bra%"}, fields("name"))
    assert "ILIKE" in sql
    assert vals[0] == "%bra%"


def test_in_operator():
    sql, vals = compile({"field": "name", "op": "in", "value": ["a", "b"]}, fields("name"))
    assert "ANY($1)" in sql
    assert vals == (["a", "b"],)


def test_nin_operator():
    sql, vals = compile({"field": "name", "op": "nin", "value": ["x"]}, fields("name"))
    assert "ALL($1)" in sql


def test_between():
    sql, vals = compile({"field": "price", "op": "between", "value": [10, 50]}, fields(("price", "decimal")))
    assert "BETWEEN $1 AND $2" in sql
    assert vals == (10, 50)


def test_is_null_true():
    sql, _ = compile({"field": "name", "op": "is_null", "value": True}, fields("name"))
    assert "IS NULL" in sql
    assert "NOT" not in sql


def test_is_null_false():
    sql, _ = compile({"field": "name", "op": "is_null", "value": False}, fields("name"))
    assert "IS NOT NULL" in sql


def test_contains_json():
    sql, vals = compile({"field": "meta", "op": "contains", "value": {"k": "v"}}, fields(("meta", "json")))
    assert "@>" in sql


# ── Composite nodes ───────────────────────────────────────────────────────────

def test_and_node():
    node = {
        "and": [
            {"field": "name", "op": "ilike", "value": "%br%"},
            {"field": "name", "op": "neq",   "value": "British Virgin Islands"},
        ]
    }
    sql, vals = compile(node, fields("name"))
    assert sql.startswith("(")
    assert " AND " in sql
    assert len(vals) == 2


def test_or_node():
    node = {"or": [
        {"field": "name", "op": "eq", "value": "Brazil"},
        {"field": "name", "op": "eq", "value": "Bolivia"},
    ]}
    sql, vals = compile(node, fields("name"))
    assert " OR " in sql
    assert len(vals) == 2


def test_nested_and_or():
    node = {
        "and": [
            {"field": "name", "op": "ilike", "value": "%br%"},
            {"or": [
                {"field": "name", "op": "eq", "value": "Brazil"},
                {"field": "name", "op": "eq", "value": "Britain"},
            ]},
        ]
    }
    sql, vals = compile(node, fields("name"))
    assert "AND" in sql
    assert "OR" in sql
    assert len(vals) == 3


# ── Security: values never appear in SQL ─────────────────────────────────────

def test_sql_injection_value_is_parameterized():
    """SQL injection payload must appear only in params, never in the SQL string."""
    evil = "'; DROP TABLE countries; --"
    sql, vals = compile({"field": "name", "op": "eq", "value": evil}, fields("name"))
    assert evil not in sql
    assert "$1" in sql
    assert vals[0] == evil


def test_unknown_field_raises():
    with pytest.raises(EngineError) as exc:
        compile({"field": "evil_column", "op": "eq", "value": "x"}, fields("name"))
    assert exc.value.code == "unknown_field"


def test_unknown_operator_raises():
    with pytest.raises(EngineError) as exc:
        compile({"field": "name", "op": "LIKE; DROP TABLE--", "value": "%"}, fields("name"))
    assert exc.value.code == "validation_failed"


def test_empty_field_name_raises():
    with pytest.raises(EngineError) as exc:
        compile({"field": "", "op": "eq", "value": "x"}, fields("name"))
    assert exc.value.code == "unknown_field"


def test_ilike_on_number_raises():
    with pytest.raises(EngineError) as exc:
        compile({"field": "price", "op": "ilike", "value": "%10%"}, fields(("price", "number")))
    assert exc.value.code == "validation_failed"


# ── Depth / leaf count limits ─────────────────────────────────────────────────

def test_max_nesting_depth():
    node = {"field": "name", "op": "eq", "value": "x"}
    for _ in range(6):      # wrap 6 levels deep (limit is 5)
        node = {"and": [node]}
    with pytest.raises(EngineError) as exc:
        compile(node, fields("name"))
    assert exc.value.code == "validation_failed"


def test_empty_and_returns_true():
    sql, vals = compile({"and": []}, fields("name"))
    assert sql == "TRUE"
    assert vals == ()


def test_empty_or_returns_false():
    sql, vals = compile({"or": []}, fields("name"))
    assert sql == "FALSE"
    assert vals == ()


# ── i18n column references ────────────────────────────────────────────────────

def test_i18n_field_uses_pim_tr():
    f   = make_field("label", "i18n_text")
    sql, vals = compile({"field": "label", "op": "ilike", "value": "%foo%"}, {"label": f}, locale="es")
    assert "pim.tr(t.\"label\", 'es')" in sql
    assert "DROP" not in sql


def test_i18n_locale_injection_escaped():
    """A locale with a single quote must not break the SQL."""
    f   = make_field("label", "i18n_text")
    # A locale value crafted to inject SQL via the locale literal
    sql, _ = compile({"field": "label", "op": "eq", "value": "x"}, {"label": f}, locale="en' OR '1'='1")
    # The injected quote should have been stripped
    assert "OR '1'='1'" not in sql
