"""i18n helpers for the API engine.

The pim.tr() PostgreSQL function resolves a JSONB field to a single string
using a want-then-fallback chain. This module provides Python-side equivalents
for use after data has already been fetched.
"""
from __future__ import annotations

from typing import Any

import asyncpg


async def resolve_locale_chain(
    locale: str,
    db: asyncpg.Connection,
) -> tuple[str, str]:
    """Return (want, fallback) for pim.tr(col, want, fallback) SQL calls.

    Queries pim.locales to find the declared fallback for the requested locale.
    Defaults to 'en' when the locale is unknown or has no fallback configured.
    """
    if not locale:
        return "en", "en"
    row = await db.fetchrow(
        "SELECT fallback_code FROM pim.locales WHERE code = $1 AND is_active = TRUE",
        locale,
    )
    fallback = "en"
    if row and row["fallback_code"]:
        fallback = row["fallback_code"]
    return locale, fallback


def flatten_i18n(value: Any, locale: str, fallback: str = "en") -> Any:
    """Resolve a JSONB dict to a single string using locale → fallback → 'en'."""
    if not isinstance(value, dict):
        return value
    return value.get(locale) or value.get(fallback) or value.get("en") or ""


def flatten_row(
    row:              dict,
    i18n_field_names: set[str],
    locale:           str,
    fallback:         str,
) -> dict:
    """Return a copy of row with every i18n field flattened to a string."""
    out: dict = {}
    for k, v in row.items():
        if k in i18n_field_names:
            out[k] = flatten_i18n(v, locale, fallback)
        else:
            out[k] = v
    return out


def merge_i18n(existing: dict | None, patch: dict) -> dict:
    """Merge a partial locale patch over the existing JSONB value (equivalent to ||)."""
    base = dict(existing or {})
    base.update(patch)
    return base
