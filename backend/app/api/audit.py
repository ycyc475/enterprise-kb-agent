import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.router import ok
from app.core.db import get_db
from app.services import audit, auth

router = APIRouter(prefix="/api/v1/admin")


@router.get("/audit-events")
async def events(
    request: Request,
    entity_type: Literal["rule", "document", "account", "profile", "knowledge_base"] | None = None,
    entity_id: uuid.UUID | None = None,
    action: str | None = Query(default=None, max_length=64),
    before_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    context=Depends(auth.admin_context),
    db: AsyncSession = Depends(get_db),
):
    return ok(
        request,
        await audit.page(
            db,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            before_id=before_id,
            limit=limit,
        ),
    )
