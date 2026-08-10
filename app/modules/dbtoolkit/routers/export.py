"""
Module: toolkit.routers.export
Purpose: HTTP handlers for toolkit Excel export/templates.
         Business logic lives in export_service.
"""
import logging
from io import BytesIO

from fastapi import APIRouter, Body, Depends
from fastapi.responses import StreamingResponse
import asyncpg

from app.core.database import get_db
from app.core.deps import get_current_admin
from app.core.response import err
from app.modules.dbtoolkit.services import export_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/toolkit/export", tags=["Toolkit - Export"])


@router.post("/template")
async def export_template(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    try:
        family_code = (body.get("family_code") or "").strip()
        endpoint = (body.get("endpoint") or "").strip()
        if not family_code:
            return err("family_code is required")
        if not endpoint:
            return err("endpoint is required")

        content, filename = await export_service.build_template(
            db,
            family_code=family_code,
            endpoint_path=endpoint,
        )
        return StreamingResponse(
            BytesIO(content),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("export_template failed")
        return err(str(e))


@router.post("/data")
async def export_data(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    try:
        family_code = (body.get("family_code") or "").strip()
        endpoint = (body.get("endpoint") or "").strip()
        if not family_code:
            return err("family_code is required")
        if not endpoint:
            return err("endpoint is required")

        content, filename = await export_service.build_data_export(
            db,
            family_code=family_code,
            endpoint_path=endpoint,
        )
        return StreamingResponse(
            BytesIO(content),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("export_data failed")
        return err(str(e))