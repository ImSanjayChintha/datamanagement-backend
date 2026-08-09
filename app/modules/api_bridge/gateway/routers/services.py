"""
Gateway service registry router — admin-only endpoint to list registered services.

Used by the frontend EndpointFormPage to populate the service selector dropdown
when db_type = "service" is chosen.
"""
import logging

from fastapi import APIRouter, Depends

from app.core.deps import get_current_admin
from app.core.response import ok, err
from app.modules.api_bridge.services.registry import list_services

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/gateway/services", tags=["Gateway"])


@router.post("/list")
async def list_registered_services(admin: dict = Depends(get_current_admin)):
    """Return all services registered in the service registry."""
    try:
        return ok(list_services())
    except Exception as exc:
        logger.error("list_registered_services: %s", exc)
        return err(str(exc))
