"""
PriceCalculatorService — Example 2

Pure-computation service: takes a list of base prices and returns
tax / margin / combined tiers for each.  No database access required —
demonstrates that services can do arbitrary Python logic without SQL.

Gateway endpoint example
------------------------
  Method : POST
  URL    : /api/v1/run/services/price-calculator
  db_type: service
  db_object: price_calculator

Body
----
  {
    "prices":     [100.00, 250.00, 499.99],
    "tax_rate":   0.21,
    "margin_pct": 0.30
  }

Response
--------
  {
    "success": true,
    "data": {
      "tax_rate": 0.21,
      "margin_pct": 0.30,
      "items": [
        {
          "base":                100.00,
          "with_tax":            121.00,
          "with_margin":         130.00,
          "with_tax_and_margin": 157.30
        },
        ...
      ]
    }
  }
"""
from __future__ import annotations

from typing import Any

import asyncpg

from app.modules.api_bridge.services.base import ServiceBase
from app.modules.api_bridge.services.registry import register


@register
class PriceCalculatorService(ServiceBase):
    name        = "price_calculator"
    description = "Calculates price tiers (net, tax, margin) for a list of base prices"

    async def execute(
        self,
        body:        dict[str, Any],
        path_params: dict[str, str],
        db:          asyncpg.Connection,
        user_email:  str | None,
    ) -> Any:
        prices     = body.get("prices",     [])
        tax_rate   = float(body.get("tax_rate",   0.21))
        margin_pct = float(body.get("margin_pct", 0.30))

        if not isinstance(prices, list):
            raise ValueError("'prices' must be an array of numbers.")
        if not (0.0 <= tax_rate <= 1.0):
            raise ValueError("'tax_rate' must be between 0 and 1 (e.g. 0.21 for 21%).")
        if not (0.0 <= margin_pct <= 1.0):
            raise ValueError("'margin_pct' must be between 0 and 1 (e.g. 0.30 for 30%).")

        items = []
        for raw in prices:
            try:
                p = float(raw)
            except (TypeError, ValueError):
                raise ValueError(f"Invalid price value: {raw!r}")
            items.append(
                {
                    "base":                round(p, 4),
                    "with_tax":            round(p * (1 + tax_rate), 4),
                    "with_margin":         round(p * (1 + margin_pct), 4),
                    "with_tax_and_margin": round(p * (1 + tax_rate) * (1 + margin_pct), 4),
                }
            )

        return {
            "tax_rate":   tax_rate,
            "margin_pct": margin_pct,
            "items":      items,
        }
