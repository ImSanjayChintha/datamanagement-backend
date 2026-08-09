"""
Abstract base class for gateway service handlers.

A service receives a request body and optional path parameters, executes
arbitrary Python logic (DB queries, external API calls, calculations, etc.),
and returns a JSON-serialisable dict that becomes the response data payload.

Usage
-----
from app.modules.api_bridge.services.base import ServiceBase
from app.modules.api_bridge.services.registry import register

@register
class MyService(ServiceBase):
    name        = "my_service"
    description = "Does something useful"

    async def execute(self, body, path_params, db, user_email):
        ...
        return {"result": ...}
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import asyncpg


class ServiceBase(ABC):
    """Base class every registered service must inherit from.

    Class attributes
    ----------------
    name : str
        Unique registry key — must match ``db_object`` on the gateway endpoint.
    description : str
        Human-readable description shown in the admin service list.
    """

    name: str
    description: str = ""

    @abstractmethod
    async def execute(
        self,
        body:        dict[str, Any],
        path_params: dict[str, str],
        db:          asyncpg.Connection,
        user_email:  str | None,
    ) -> Any:
        """Execute service logic and return a JSON-serialisable value.

        Parameters
        ----------
        body        : merged request payload (query params for GET, JSON body for POST/PATCH/DELETE)
        path_params : URL template values extracted from the path (e.g. ``{id}``)
        db          : live asyncpg connection — use within a transaction if needed
        user_email  : email of the authenticated caller (None when unauthenticated)
        """
