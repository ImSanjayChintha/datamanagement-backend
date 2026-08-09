"""
Service registry — maps service names to singleton ServiceBase instances.

Services self-register via the @register decorator when their module is imported.
Import ``app.modules.api_bridge.services.loader`` once at startup to trigger all
registrations.
"""
from __future__ import annotations

from typing import Type

from .base import ServiceBase

_registry: dict[str, ServiceBase] = {}


def register(cls: Type[ServiceBase]) -> Type[ServiceBase]:
    """Class decorator — instantiates the service and adds it to the registry."""
    instance = cls()
    if not hasattr(instance, "name") or not instance.name:
        raise ValueError(f"Service class {cls.__name__} must define a non-empty 'name' attribute.")
    if instance.name in _registry:
        raise ValueError(
            f"Service name '{instance.name}' is already registered by "
            f"{type(_registry[instance.name]).__name__}. Each service must have a unique name."
        )
    _registry[instance.name] = instance
    return cls


def get_service(name: str) -> ServiceBase | None:
    """Return the service instance for *name*, or None if not registered."""
    return _registry.get(name)


def list_services() -> list[dict]:
    """Return a list of dicts describing every registered service."""
    return [
        {"name": svc.name, "description": getattr(svc, "description", "")}
        for svc in _registry.values()
    ]
