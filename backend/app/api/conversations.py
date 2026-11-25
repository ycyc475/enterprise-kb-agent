import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.router import ok
from app.chat_protocol import ConversationCreate, ConversationPatch, MessageSubmit, TripPatch
from app.core.db import get_db
from app.models import Conversation, RunEvent
from app.services import auth
from app.services import conversations as service

router = APIRouter(prefix="/api/v1")


@router.get("/chat/config")
async def config(request: Request, context=Depends(auth.ready_context)):
    return ok(
        request,
        {
            "ready": request.app.state.chat_provider.ready,
            "model": request.app.state.settings.chat_model,
            "limits": {
                "search": request.app.state.settings.max_search_attempts,
                "model": request.app.state.settings.max_model_attempts,
                "seconds": request.app.state.settings.max_run_seconds,
            },
        },
    )


@router.get("/conversations")
async def conversations(
    request: Request,
    context=Depends(auth.ready_context),
    db: AsyncSession = Depends(get_db),
    offset: int = Query(default=0, ge=0, le=10000),
):
    values = (
        await db.scalars(
            select(Conversation)
            .where(Conversation.owner_id == context.user.id, Conversation.deleted_at.is_(None))
            .order_by(Conversation.updated_at.desc(), Conversation.id)
            .offset(offset)
            .limit(30)
        )
    ).all()
    return ok(
        request,
        {
            "items": [await service.conversation_out(db, conv, context.user) for conv in values],
            "next_offset": offset + 30 if len(values) == 30 else None,
        },
    )


@router.post("/conversations", status_code=201)
async def create(
    body: ConversationCreate,
    request: Request,
    context=Depends(auth.ready_context),
    db: AsyncSession = Depends(get_db),
):
    return ok(request, await service.create(db, context.user, body.title, body.knowledge_base_ids))


@router.get("/conversations/{conversation_id}")
async def read(
    conversation_id: uuid.UUID,
    request: Request,
    context=Depends(auth.ready_context),
    db: AsyncSession = Depends(get_db),
):
    return ok(
        request,
        await service.conversation_out(
            db, await service.owned(db, context.user.id, conversation_id), context.user
        ),
    )


@router.patch("/conversations/{conversation_id}")
async def patch(
    conversation_id: uuid.UUID,
    body: ConversationPatch,
    request: Request,
    context=Depends(auth.ready_context),
    db: AsyncSession = Depends(get_db),
):
    return ok(request, await service.update(db, context.user, conversation_id, body))


@router.patch("/conversations/{conversation_id}/trip-context")
async def trip(
    conversation_id: uuid.UUID,
    body: TripPatch,
    request: Request,
    context=Depends(auth.ready_context),
    db: AsyncSession = Depends(get_db),
):
    return ok(request, await service.update(db, context.user, conversation_id, body))


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete(
    conversation_id: uuid.UUID,
    context=Depends(auth.ready_context),
    db: AsyncSession = Depends(get_db),
):
    await service.remove(db, context.user, conversation_id)


@router.get("/conversations/{conversation_id}/messages")
async def messages(
    conversation_id: uuid.UUID,
    request: Request,
    before: datetime | None = None,
    context=Depends(auth.ready_context),
    db: AsyncSession = Depends(get_db),
):
    return ok(
        request,
        await service.messages(
            db, request.app.state.settings, context.user, conversation_id, before
        ),
    )


@router.post("/conversations/{conversation_id}/messages", status_code=202)
async def submit(
    conversation_id: uuid.UUID,
    body: MessageSubmit,
    request: Request,
    context=Depends(auth.ready_context),
    db: AsyncSession = Depends(get_db),
):
    return ok(request, await service.submit(request.app, db, context, conversation_id, body))


@router.get("/runs/{run_id}")
async def run(
    run_id: uuid.UUID,
    request: Request,
    context=Depends(auth.ready_context),
    db: AsyncSession = Depends(get_db),
):
    return ok(request, service.run_out(await service.own_run(db, context.user.id, run_id)))


@router.get("/runs/{run_id}/events")
async def events(
    run_id: uuid.UUID,
    request: Request,
    after_seq: int = Query(default=0, ge=0),
    context=Depends(auth.ready_context),
    db: AsyncSession = Depends(get_db),
):
    await service.own_run(db, context.user.id, run_id)
    rows = (
        await db.scalars(
            select(RunEvent)
            .where(RunEvent.run_id == run_id, RunEvent.seq > after_seq)
            .order_by(RunEvent.seq)
            .limit(100)
        )
    ).all()
    return ok(
        request,
        {
            "items": [
                {
                    "seq": row.seq,
                    "type": row.event_type,
                    "label": row.label,
                    "duration_ms": row.duration_ms,
                    "meta": row.meta,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in rows
            ],
            "next_seq": rows[-1].seq if rows else after_seq,
        },
    )


@router.get("/runs/{run_id}/trace")
async def trace(
    run_id: uuid.UUID,
    request: Request,
    context=Depends(auth.ready_context),
    db: AsyncSession = Depends(get_db),
):
    run = await service.own_run(db, context.user.id, run_id)
    return ok(request, await service.run_trace(db, run))


@router.post("/runs/{run_id}/cancel", status_code=202)
async def cancel(
    run_id: uuid.UUID,
    request: Request,
    context=Depends(auth.ready_context),
    db: AsyncSession = Depends(get_db),
):
    run = await service.own_run(db, context.user.id, run_id)
    if run.status in service.ACTIVE:
        run.cancel_requested = True
        if run.status == "queued":
            run.status, run.stop_reason = "cancelled", "CANCELLED"
        await db.commit()
    return ok(request, service.run_out(run))


@router.get("/citations/{source_id}")
async def citation(
    source_id: uuid.UUID,
    request: Request,
    context=Depends(auth.ready_context),
    db: AsyncSession = Depends(get_db),
):
    return ok(
        request, await service.citation(db, request.app.state.settings, context.user, source_id)
    )
