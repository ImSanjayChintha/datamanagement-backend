"""
Standardized API response helpers.

Every endpoint wraps its return value with ok() or err() so callers
always receive:
  { "success": true,  "data": <payload>, "error": null  }
  { "success": false, "data": null,       "error": "<message>" }
"""
import logging

logger = logging.getLogger(__name__)


def ok(data=None) -> dict:
    return {"success": True, "data": data, "error": None}


def err(message: str, *, log: bool = False) -> dict:
    if log:
        logger.error(message)
    return {"success": False, "data": None, "error": str(message)}
