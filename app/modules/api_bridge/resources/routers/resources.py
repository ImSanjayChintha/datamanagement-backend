"""
Module: api_bridge.resources.routers.resources
Purpose: CRUD + connection-test endpoints for API resource connections.
"""
import logging

import asyncpg
from fastapi import APIRouter, Body, Depends

from app.core.database import get_db
from app.core.deps import get_current_admin
from app.core.response import ok, err
from app.modules.api_bridge.resources.services import resource_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/api-bridge/resources", tags=["API Bridge - Resources"])


@router.post("/list")
async def list_resources(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    try:
        page      = max(1, int(body.get("page")      or 1))
        page_size = max(1, min(1000, int(body.get("page_size") or 100)))
        offset    = (page - 1) * page_size
        result    = await resource_service.list_resources(
            db,
            is_active=body.get("is_active"),
            search=body.get("search") or None,
            limit=page_size,
            offset=offset,
        )
        total = result["total"]
        pages = max(1, (total + page_size - 1) // page_size)
        return ok({
            "rows":      result["rows"],
            "total":     total,
            "page":      page,
            "page_size": page_size,
            "pages":     pages,
        })
    except Exception as e:
        logger.exception("list_resources failed")
        return err(str(e))


@router.post("/get")
async def get_resource(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    try:
        resource_id = body.get("id")
        if not resource_id:
            return err("id is required")
        result = await resource_service.get_resource(db, int(resource_id))
        if not result:
            return err("Resource not found")
        return ok(result)
    except Exception as e:
        logger.exception("get_resource failed")
        return err(str(e))


@router.post("/create")
async def create_resource(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    try:
        result = await resource_service.create_resource(db, body, user=admin["email"])
        return ok(result)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("create_resource failed")
        return err(str(e))


@router.post("/update")
async def update_resource(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    try:
        resource_id = body.get("id")
        if not resource_id:
            return err("id is required")
        result = await resource_service.update_resource(
            db, int(resource_id), body, user=admin["email"]
        )
        return ok(result)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("update_resource failed")
        return err(str(e))


@router.post("/delete")
async def delete_resource(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    try:
        resource_id = body.get("id")
        if not resource_id:
            return err("id is required")
        await resource_service.delete_resource(db, int(resource_id))
        return ok({"deleted": True})
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("delete_resource failed")
        return err(str(e))


@router.post("/test-connection")
async def test_connection(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    try:
        resource_id = body.get("id")
        if not resource_id:
            return err("id is required")
        result = await resource_service.test_connection(db, int(resource_id))
        return ok(result)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("test_connection failed")
        return err(str(e))
