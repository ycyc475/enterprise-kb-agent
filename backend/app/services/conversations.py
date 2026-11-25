import hashlib
import json
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.core.errors import ApiError
from app.core.security import now
from app.core.telemetry import summarize_trace
from app.models import (
    AgentRun,
    Conversation,
    ConversationScope,
    DocumentChunk,
    EmployeeProfile,
    Message,
    MessageSource,
    RunEvent,
    UsageRecord,
)
from app.services.auth import allowed_kbs
from app.services.chat_errors import public_error
from app.services.chat_routing import route_question
from app.services.documents import hit_out, visible_chunks

ACTIVE = ("queued", "running")


async def owned(db, user_id, conversation_id, *, lock=False):
    value = await db.scalar(
        select(Conversation)
        .where(
            Conversation.id == conversation_id,
            Conversation.owner_id == user_id,
            Conversation.deleted_at.is_(None),
        )
        .with_for_update()
        if lock
        else select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.owner_id == user_id,
            Conversation.deleted_at.is_(None),
        )
    )
    if value is None:
        raise ApiError(404, "NOT_FOUND", "会话不存在或不可访问")
    return value


async def scopes(db, conversation_id):
    return list(
        (
            await db.scalars(
                select(ConversationScope.kb_id)
                .where(ConversationScope.conversation_id == conversation_id)
                .order_by(ConversationScope.kb_id)
            )
        ).all()
    )


async def validate_scopes(db, user, ids):
    allowed = {kb.id for kb in await allowed_kbs(db, user)}
    if not ids or not set(ids).issubset(allowed):
        raise ApiError(409, "SCOPE_CHANGED", "请选择当前获授权且已启用的知识库")


async def conversation_out(db, conv, user):
    ids = await scopes(db, conv.id)
    allowed = {kb.id for kb in await allowed_kbs(db, user)}
    active = await db.scalar(
        select(AgentRun.id).where(AgentRun.conversation_id == conv.id, AgentRun.status.in_(ACTIVE))
    )
    latest = await db.scalar(
        select(AgentRun)
        .where(AgentRun.conversation_id == conv.id)
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        .limit(1)
    )
    return {
        "id": str(conv.id),
        "title": conv.title,
        "knowledge_base_ids": [str(i) for i in ids],
        "trip_context": conv.trip_context,
        "expense_context": conv.expense_context,
        "context_revision": conv.context_revision,
        "scope_changed": not set(ids).issubset(allowed),
        "active_run_id": str(active) if active else None,
        "latest_run": run_out(latest) if latest else None,
        "updated_at": conv.updated_at.isoformat(),
    }


async def create(db, user, title, kb_ids):
    ids = kb_ids if kb_ids is not None else [kb.id for kb in await allowed_kbs(db, user)]
    await validate_scopes(db, user, ids)
    conv = Conversation(owner_id=user.id, title=title, trip_context={})
    db.add(conv)
    await db.flush()
    db.add_all([ConversationScope(conversation_id=conv.id, kb_id=i) for i in set(ids)])
    await db.commit()
    return await conversation_out(db, conv, user)


async def update(db, user, conversation_id, body):
    conv = await owned(db, user.id, conversation_id, lock=True)
    if conv.context_revision != body.expected_revision:
        raise ApiError(409, "CONTEXT_VERSION_CONFLICT", "会话条件已更新，请刷新后重试")
    if await db.scalar(
        select(AgentRun.id).where(AgentRun.conversation_id == conv.id, AgentRun.status.in_(ACTIVE))
    ):
        raise ApiError(409, "ACTIVE_RUN_EXISTS", "请先结束当前问答任务再修改会话")
    if hasattr(body, "knowledge_base_ids") and body.knowledge_base_ids is not None:
        await validate_scopes(db, user, body.knowledge_base_ids)
        await db.execute(
            delete(ConversationScope).where(ConversationScope.conversation_id == conv.id)
        )
        db.add_all(
            [
                ConversationScope(conversation_id=conv.id, kb_id=i)
                for i in set(body.knowledge_base_ids)
            ]
        )
    if hasattr(body, "title") and body.title is not None:
        conv.title = body.title
    if hasattr(body, "trip_context"):
        conv.trip_context = body.trip_context.model_dump(exclude_none=True)
    conv.context_revision += 1
    conv.updated_at = now()
    await db.commit()
    return await conversation_out(db, conv, user)


async def remove(db, user, conversation_id):
    conv = await owned(db, user.id, conversation_id, lock=True)
    conv.deleted_at = now()
    for run in (
        await db.scalars(
            select(AgentRun).where(AgentRun.conversation_id == conv.id, AgentRun.status.in_(ACTIVE))
        )
    ).all():
        run.cancel_requested = True
        if run.status == "queued":
            run.status, run.stop_reason = "cancelled", "CONVERSATION_DELETED"
    await db.commit()


def run_out(run):
    return {
        "id": str(run.id),
        "conversation_id": str(run.conversation_id),
        "status": run.status,
        "request_message_id": str(run.request_message_id),
        "response_message_id": str(run.response_message_id) if run.response_message_id else None,
        "search_attempts": run.search_attempts,
        "model_attempts": run.model_attempts,
        "stop_reason": run.stop_reason,
        "deadline_at": run.deadline_at.isoformat(),
        "limits": {
            "search": run.config_snapshot.get("search", 5),
            "model": run.config_snapshot.get("model_attempts", 10),
            "seconds": run.config_snapshot.get("seconds", 90),
        },
    }


async def own_run(db, user_id, run_id):
    run = await db.scalar(
        select(AgentRun).where(AgentRun.id == run_id, AgentRun.actor_id == user_id)
    )
    if run is None:
        raise ApiError(404, "NOT_FOUND", "任务不存在或不可访问")
    await owned(db, user_id, run.conversation_id)
    return run


async def run_trace(db, run):
    events = list(
        (
            await db.scalars(
                select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.seq)
            )
        ).all()
    )
    usage_rows = list(
        (
            await db.scalars(
                select(UsageRecord)
                .where(UsageRecord.run_id == run.id)
                .order_by(UsageRecord.created_at)
            )
        ).all()
    )
    return summarize_trace(run, events, usage_rows)


async def submit(app, db, context, conversation_id, body):
    conv = await owned(db, context.user.id, conversation_id, lock=True)
    payload = {"conversation_id": str(conv.id), **body.model_dump(mode="json")}
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    existing = await db.scalar(
        select(AgentRun).where(
            AgentRun.actor_id == context.user.id,
            AgentRun.client_request_id == body.client_request_id,
        )
    )
    if existing:
        if existing.request_hash != digest:
            raise ApiError(409, "REQUEST_KEY_REUSED", "同一请求标识不能提交不同的问题或条件")
        return run_out(existing)
    if not app.state.chat_provider.ready and route_question(body.content).knowledge_question:
        raise ApiError(503, "CHAT_NOT_CONFIGURED", "对话服务尚未配置完成")
    if conv.context_revision != body.context_revision:
        raise ApiError(409, "CONTEXT_VERSION_CONFLICT", "会话条件已变化，请刷新后重新提交")
    ids = await scopes(db, conv.id)
    await validate_scopes(db, context.user, ids)
    if await db.scalar(
        select(AgentRun.id).where(AgentRun.conversation_id == conv.id, AgentRun.status.in_(ACTIVE))
    ):
        raise ApiError(409, "ACTIVE_RUN_EXISTS", "当前会话已有问答任务，请等待或取消")
    if body.trip_context is not None:
        conv.trip_context = body.trip_context.model_dump(exclude_none=True)
        conv.context_revision += 1
    if conv.title == "新会话":
        conv.title = body.content[:40]
    conv.updated_at = now()
    profile = await db.get(EmployeeProfile, context.user.id)
    if (
        body.budget_confirmed_revision is not None
        and body.budget_profile_revision != profile.revision
    ):
        raise ApiError(409, "CONTEXT_VERSION_CONFLICT", "员工资料已变化，请刷新并重新确认预算")
    message = Message(
        conversation_id=conv.id, role="user", content=body.content, status="submitted"
    )
    db.add(message)
    await db.flush()
    cfg = app.state.settings
    run = AgentRun(
        conversation_id=conv.id,
        actor_id=context.user.id,
        family_id=context.family.id,
        auth_version=context.version,
        client_request_id=body.client_request_id,
        request_hash=digest,
        request_message_id=message.id,
        status="queued",
        deadline_at=now() + timedelta(seconds=cfg.max_run_seconds),
        profile_revision=profile.revision,
        context_revision=conv.context_revision,
        config_snapshot={
            "model": cfg.chat_model,
            "embedding_model": cfg.embedding_model,
            "embedding_dimensions": cfg.embedding_dimensions,
            "search": cfg.max_search_attempts,
            "model_attempts": cfg.max_model_attempts,
            "seconds": cfg.max_run_seconds,
            "budget_confirmed": body.budget_confirmed_revision is not None,
        },
    )
    db.add(run)
    try:
        await db.flush()
        db.add(RunEvent(run_id=run.id, seq=1, event_type="queued", label="等待开始问答"))
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ApiError(
            409, "REQUEST_CONFLICT", "问题已提交或当前会话已有任务，请刷新后查看"
        ) from None
    return run_out(run)


async def visibility(db, settings, user, conversation_id, message):
    from app.services.fees import current_calculation

    if not await current_calculation(db, message.id):
        return False, [], {}
    sources = list(
        (
            await db.scalars(
                select(MessageSource)
                .where(MessageSource.message_id == message.id)
                .order_by(MessageSource.citation_index.nulls_last(), MessageSource.id)
            )
        ).all()
    )
    ids = set(await scopes(db, conversation_id)) & {k.id for k in await allowed_kbs(db, user)}
    query = visible_chunks(user, list(ids), settings)
    rows = (
        (await db.execute(query.where(DocumentChunk.id.in_([s.chunk_id for s in sources])))).all()
        if sources
        else []
    )
    found = {c.id: (c, v, d) for c, v, d in rows}
    return all(s.chunk_id in found for s in sources), sources, found


async def message_out(db, settings, user, message):
    result = {
        "id": str(message.id),
        "role": message.role,
        "status": message.status,
        "content": message.content,
        "citations": [],
        "created_at": message.created_at.isoformat(),
    }
    if message.role == "user":
        return result
    visible, sources, found = await visibility(db, settings, user, message.conversation_id, message)
    if not visible:
        return {
            **result,
            "content": None,
            "status": "source_unavailable",
            "notice": "此回答的依据已更新、失效或不再可访问，请重新提问",
        }
    result["missing_fields"] = (message.structured_result or {}).get("missing_fields", [])
    structured = message.structured_result or {}
    if structured.get("error"):
        result["error"] = structured["error"]
    elif message.status in {"failed", "partial", "timeout"}:
        previous_run = await db.scalar(
            select(AgentRun).where(AgentRun.response_message_id == message.id)
        )
        if previous_run and previous_run.stop_reason:
            result["error"] = public_error(previous_run.stop_reason)
    if structured.get("trip_proposal"):
        result["trip_proposal"] = structured["trip_proposal"]
    if structured.get("expense_proposal"):
        result["expense_proposal"] = structured["expense_proposal"]
    from app.models import Calculation
    from app.services.fees import calculation_out

    calc = await db.scalar(select(Calculation).where(Calculation.message_id == message.id))
    if calc:
        result["calculation"] = await calculation_out(db, calc)
    for source in sources:
        if source.is_cited:
            c, v, d = found[source.chunk_id]
            result["citations"].append(
                {
                    "id": str(source.id),
                    "index": source.citation_index,
                    "title": d.title,
                    "version_no": v.version_no,
                    "anchor": source.anchor_snapshot,
                }
            )
    return result


async def messages(db, settings, user, conversation_id, before=None):
    await owned(db, user.id, conversation_id)
    query = select(Message).where(Message.conversation_id == conversation_id)
    if before:
        query = query.where(Message.created_at < before)
    values = list(
        (
            await db.scalars(query.order_by(Message.created_at.desc(), Message.id.desc()).limit(50))
        ).all()
    )
    return {
        "items": [await message_out(db, settings, user, m) for m in reversed(values)],
        "next_before": values[-1].created_at.isoformat() if len(values) == 50 else None,
    }


async def citation(db, settings, user, source_id):
    source = await db.get(MessageSource, source_id)
    message = await db.get(Message, source.message_id) if source else None
    if message is None or not source.is_cited:
        raise ApiError(404, "NOT_FOUND", "引用不存在或不可访问")
    await owned(db, user.id, message.conversation_id)
    visible, _, found = await visibility(db, settings, user, message.conversation_id, message)
    if not visible:
        raise ApiError(404, "NOT_FOUND", "引用依据已失效或不可访问")
    return {
        **hit_out(*found[source.chunk_id]),
        "id": str(source.id),
        "quote": source.quote_snapshot,
    }
