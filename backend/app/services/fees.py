"""Source-backed rules and Decimal arithmetic. Models cannot supply prices or identity."""

import hashlib
import json
import uuid
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select, text

from app.core.errors import ApiError
from app.core.security import now
from app.models import (
    Calculation,
    CalculationItem,
    Document,
    DocumentChunk,
    DocumentVersion,
    EmployeeProfile,
    FeeRule,
    KnowledgeBase,
    Message,
    MessageSource,
)
from app.services import audit, conversations, documents
from app.services.auth import allowed_kbs, profile_out
from app.services.embedding import space
from app.services.management import lock_admin

LABELS = {
    "lodging": "住宿预算额度",
    "daily": "出差补贴",
    "office": "办公采购参考额度",
    "training": "培训报销参考额度",
    "hospitality": "业务招待参考额度",
}
UNITS = {"lodging": "晚", "daily": "天", "office": "套", "training": "人", "hospitality": "人"}
MISSING = {"destination": "目的地", "travel_days": "出差天数", "lodging_nights": "住宿晚数"}


def money(value):
    return str(Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


async def source(db, settings, user, chunk_id, kb_ids=None):
    if kb_ids is None:
        kb_ids = [kb.id for kb in await allowed_kbs(db, user)]
    return (
        await db.execute(
            documents.visible_chunks(user, kb_ids, settings).where(DocumentChunk.id == chunk_id)
        )
    ).first()


async def rule_out(db, settings, user, rule):
    row = await source(db, settings, user, rule.source_chunk_id)
    usable = row is not None and rule.source_quote in row[0].content
    previous = await db.get(DocumentChunk, rule.source_chunk_id)
    version = await db.get(DocumentVersion, previous.version_id) if previous else None
    return {
        "id": str(rule.id),
        "name": rule.name,
        "kind": rule.kind,
        "grade": rule.grade,
        "cities": rule.cities,
        "unit_rate": money(rule.unit_rate),
        "application_cap": money(rule.application_cap)
        if rule.application_cap is not None
        else None,
        "reimbursement_ratio": str(rule.reimbursement_ratio),
        "max_quantity": rule.max_quantity,
        "currency": "CNY",
        "unit": "night" if rule.kind == "lodging" else "day",
        "status": rule.status,
        "effective_status": rule.status if usable else "source_unavailable",
        "revision": rule.revision,
        "source_chunk_id": str(rule.source_chunk_id),
        "source_quote": rule.source_quote,
        "source": documents.hit_out(*row) if row else None,
        "confirmed_at": rule.confirmed_at.isoformat() if rule.confirmed_at else None,
        "suspension_reason": rule.suspension_reason,
        "source_document_id": str(version.document_id) if version else None,
        "source_version_no": version.version_no if version else None,
        "source_state_reason": await source_state(db, settings, rule),
    }


async def list_rules(db, settings, user):
    values = (await db.scalars(select(FeeRule).order_by(FeeRule.created_at, FeeRule.id))).all()
    return {"items": [await rule_out(db, settings, user, value) for value in values]}


async def save_rule(db, settings, actor, body, rule_id=None):
    await lock_admin(db, actor)
    row = await source(db, settings, actor.user, body.source_chunk_id)
    if row is None or body.source_quote not in row[0].content:
        raise ApiError(409, "RULE_SOURCE_INVALID", "请选择当前有效文档中的片段并保留原文连续摘录")
    rule = await db.get(FeeRule, rule_id, with_for_update=True) if rule_id else FeeRule()
    if rule is None:
        raise ApiError(404, "NOT_FOUND", "规则不存在")
    if rule_id and rule.revision != body.expected_revision:
        raise ApiError(409, "RULE_VERSION_CONFLICT", "规则已更新，请刷新后重新核对")
    before = audit.snapshot("rule", rule) if rule_id else None
    for key in [
        "name",
        "kind",
        "grade",
        "cities",
        "unit_rate",
        "source_chunk_id",
        "source_quote",
        "application_cap",
        "reimbursement_ratio",
        "max_quantity",
    ]:
        setattr(rule, key, getattr(body, key))
    if rule_id:
        rule.revision += 1
    rule.status, rule.confirmed_by, rule.confirmed_at = "draft", None, None
    rule.suspension_reason = None
    rule.updated_at = now()
    db.add(rule)
    await db.flush()
    audit.record(
        db,
        "rule",
        rule.id,
        "rule.updated" if rule_id else "rule.created",
        actor,
        before=before,
        after=rule,
    )
    await db.commit()
    return await rule_out(db, settings, actor.user, rule)


async def transition(db, settings, actor, rule_id, body, activate):
    await lock_admin(db, actor)
    rule = await db.get(FeeRule, rule_id, with_for_update=True)
    if rule is None:
        raise ApiError(404, "NOT_FOUND", "规则不存在")
    if rule.revision != body.expected_revision:
        raise ApiError(409, "RULE_VERSION_CONFLICT", "规则已更新，请刷新后重新核对")
    before = audit.snapshot("rule", rule)
    if activate:
        await validate_activation(db, settings, actor.user, rule)
        rule.status, rule.confirmed_by, rule.confirmed_at = "active", actor.user.id, now()
        rule.suspension_reason = None
    else:
        rule.status, rule.suspension_reason = "suspended", "ADMIN_SUSPENDED"
    rule.revision += 1
    rule.updated_at = now()
    audit.record(
        db,
        "rule",
        rule.id,
        "rule.activated" if activate else "rule.suspended",
        actor,
        before=before,
        after=rule,
    )
    await db.commit()
    return await rule_out(db, settings, actor.user, rule)


async def validate_activation(db, settings, user, rule):
    row = await source(db, settings, user, rule.source_chunk_id)
    if row is None or rule.source_quote not in row[0].content:
        raise ApiError(409, "RULE_SOURCE_INVALID", "规则来源已失效，请先关联当前有效条款")
    for other in (
        await db.scalars(
            select(FeeRule).where(
                FeeRule.status == "active", FeeRule.kind == rule.kind, FeeRule.id != rule.id
            )
        )
    ).all():
        overlaps = rule.kind != "lodging" or (
            other.grade == rule.grade and set(other.cities) & set(rule.cities)
        )
        other_source = await source(db, settings, user, other.source_chunk_id) if overlaps else None
        if other_source and other.source_quote in other_source[0].content:
            raise ApiError(409, "RULE_CONFLICT", "已有相同适用范围的有效规则，请先暂停旧规则并核对")


async def invalidate(db, rule, reason, actor=None):
    before = audit.snapshot("rule", rule)
    rule.status, rule.suspension_reason = "suspended", reason
    rule.revision += 1
    rule.updated_at = now()
    audit.record(db, "rule", rule.id, "rule.source_invalidated", actor, before=before, after=rule)


async def suspend_version(db, version_id, *, reason="SOURCE_CHANGED", actor=None):
    # Called in the same transaction as current-version publication/deletion.
    rules = (
        await db.scalars(
            select(FeeRule)
            .where(
                FeeRule.status == "active",
                FeeRule.source_chunk_id.in_(
                    select(DocumentChunk.id).where(DocumentChunk.version_id == version_id)
                ),
            )
            .with_for_update()
        )
    ).all()
    for rule in rules:
        await invalidate(db, rule, reason, actor)


async def suspend_kb(db, kb_id, actor):
    ids = (
        select(DocumentChunk.id)
        .join(DocumentVersion, DocumentVersion.id == DocumentChunk.version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(Document.kb_id == kb_id)
    )
    for rule in (
        await db.scalars(
            select(FeeRule)
            .where(FeeRule.status == "active", FeeRule.source_chunk_id.in_(ids))
            .with_for_update()
        )
    ).all():
        await invalidate(db, rule, "KNOWLEDGE_BASE_DISABLED", actor)


async def source_state(db, settings, rule):
    chunk = await db.get(DocumentChunk, rule.source_chunk_id)
    version = await db.get(DocumentVersion, chunk.version_id) if chunk else None
    doc = await db.get(Document, version.document_id) if version else None
    if doc is None or doc.deleted_at:
        return "DOCUMENT_DELETED"
    kb = await db.get(KnowledgeBase, doc.kb_id)
    if not kb.is_active:
        return "KNOWLEDGE_BASE_DISABLED"
    if doc.current_version_id != version.id:
        return "SOURCE_CHANGED"
    if not documents.effective(version):
        return (
            "SOURCE_EXPIRED"
            if version.effective_until and version.effective_until <= now()
            else "SOURCE_NOT_EFFECTIVE"
        )
    if version.embedding_space != space(settings):
        return "INDEX_CONFIG_CHANGED"
    if rule.source_quote not in chunk.content:
        return "SOURCE_QUOTE_CHANGED"
    return None


async def reconcile_sources(db, settings):
    # Serialize with administration; the document worker performs this sweep periodically.
    await db.execute(text("SELECT pg_advisory_xact_lock(8174201)"))
    for rule in (
        await db.scalars(select(FeeRule).where(FeeRule.status == "active").with_for_update())
    ).all():
        reason = await source_state(db, settings, rule)
        if reason:
            await invalidate(db, rule, reason)


async def review_source(db, settings, user, rule_id):
    rule = await db.get(FeeRule, rule_id)
    if rule is None:
        raise ApiError(404, "NOT_FOUND", "规则不存在")
    chunk = await db.get(DocumentChunk, rule.source_chunk_id)
    old_version = await db.get(DocumentVersion, chunk.version_id)
    doc = await db.get(Document, old_version.document_id)
    rows = list(
        (
            await db.execute(
                documents.visible_chunks(user, [doc.kb_id], settings)
                .where(Document.id == doc.id)
                .order_by(DocumentChunk.ordinal)
            )
        ).all()
    )
    return {
        "rule": await rule_out(db, settings, user, rule),
        "previous_source": {
            "title": doc.title,
            "version_no": old_version.version_no,
            "quote": rule.source_quote,
            "anchor": chunk.anchor,
        },
        "current_version_id": str(doc.current_version_id) if rows else None,
        "candidates": [documents.hit_out(*row) for row in rows],
    }


async def recover(db, settings, actor, rule_id, body):
    await lock_admin(db, actor)
    rule = await db.get(FeeRule, rule_id, with_for_update=True)
    if rule is None:
        raise ApiError(404, "NOT_FOUND", "规则不存在")
    if rule.revision != body.expected_revision:
        raise ApiError(409, "RULE_VERSION_CONFLICT", "规则已更新，请重新核对")
    if rule.status == "active":
        raise ApiError(409, "RECOVERY_NOT_REQUIRED", "规则仍处于启用状态，请刷新状态或使用编辑")
    old_chunk = await db.get(DocumentChunk, rule.source_chunk_id)
    old_version = await db.get(DocumentVersion, old_chunk.version_id)
    row = await source(db, settings, actor.user, body.source_chunk_id)
    if (
        row is None
        or row[1].id != body.expected_source_version_id
        or row[2].id != old_version.document_id
        or body.source_quote not in row[0].content
    ):
        raise ApiError(
            409, "RULE_SOURCE_INVALID", "来源已变化，请选择同一文档的当前有效原文重新核对"
        )
    before = audit.snapshot("rule", rule)
    rule.source_chunk_id, rule.source_quote, rule.unit_rate = (
        body.source_chunk_id,
        body.source_quote,
        body.unit_rate,
    )
    await validate_activation(db, settings, actor.user, rule)
    rule.status, rule.suspension_reason = "active", None
    rule.confirmed_by, rule.confirmed_at = actor.user.id, now()
    rule.revision += 1
    rule.updated_at = now()
    audit.record(db, "rule", rule.id, "rule.recovered", actor, before=before, after=rule)
    await db.commit()
    return await rule_out(db, settings, actor.user, rule)


async def prepare(db, settings, user, conv):
    profile = await db.get(EmployeeProfile, user.id, populate_existing=True)
    if profile is None:
        raise ApiError(409, "PROFILE_REQUIRED", "请联系管理员补全本人资料")
    trip = conv.trip_context
    missing = [key for key in MISSING if trip.get(key) is None]
    kb_ids = await conversations.scopes(db, conv.id)
    await conversations.validate_scopes(db, user, kb_ids)
    items, evidence, reasons = [], {}, []
    rules = (
        await db.scalars(
            select(FeeRule).where(FeeRule.status == "active").order_by(FeeRule.id).with_for_update()
        )
    ).all()
    for kind, quantity_key in [("lodging", "lodging_nights"), ("daily", "travel_days")]:
        if trip.get(quantity_key) is None or (
            kind == "lodging" and trip.get("destination") is None
        ):
            continue
        candidates = []
        for rule in rules:
            if rule.kind != kind or (
                kind == "lodging"
                and (rule.grade != profile.grade or trip["destination"] not in rule.cities)
            ):
                continue
            row = await source(db, settings, user, rule.source_chunk_id, kb_ids)
            if row and rule.source_quote in row[0].content:
                candidates.append((rule, row))
        if len(candidates) != 1:
            reasons.append(
                {
                    "kind": kind,
                    "code": "RULE_CONFLICT" if candidates else "RULE_UNAVAILABLE",
                    "message": f"{LABELS[kind]}存在冲突，需管理员核对"
                    if candidates
                    else f"当前授权范围内没有适用且有效的{LABELS[kind]}规则",
                }
            )
            continue
        rule, row = candidates[0]
        quantity = trip[quantity_key]
        amount = (rule.unit_rate * quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        snapshot = {
            "id": str(rule.id),
            "revision": rule.revision,
            "name": rule.name,
            "source_chunk_id": str(rule.source_chunk_id),
            "source_quote": rule.source_quote,
            "grade": rule.grade,
            "cities": rule.cities,
            "unit_rate": money(rule.unit_rate),
            "source": {
                "title": row[2].title,
                "version_no": row[1].version_no,
                "anchor": row[0].anchor,
                "quote": rule.source_quote,
            },
        }
        items.append(
            {
                "kind": kind,
                "label": LABELS[kind],
                "quantity": quantity,
                "unit": "晚" if kind == "lodging" else "天",
                "unit_rate": money(rule.unit_rate),
                "amount": money(amount),
                "formula": f"{money(rule.unit_rate)}元 × {quantity}{'晚' if kind == 'lodging' else '天'} = {money(amount)}元",
                "rule_id": str(rule.id),
                "rule_revision": rule.revision,
                "rule_snapshot": snapshot,
            }
        )
        evidence[str(row[0].id)] = {**documents.hit_out(*row), "quote": rule.source_quote[:800]}
    complete = not missing and not reasons and len(items) == 2
    result = {
        "status": "complete" if complete else "needs_information" if missing else "blocked",
        "basis": "policy_standard_budget",
        "currency": "CNY",
        "profile": profile_out(profile),
        "trip_context": trip,
        "context_revision": conv.context_revision,
        "items": items,
        "missing_fields": missing,
        "blocked_reasons": reasons,
        "total_amount": money(sum(Decimal(i["amount"]) for i in items)) if complete else None,
    }
    return result, evidence


def text_result(result):
    if result.get("expense_context"):
        from app.services.expenses import text_result as expense_text

        return expense_text(result)
    lines = [
        "按制度标准估算预算（虚构演示，不是实际报销或审批金额）。",
        f"职级：{result['profile']['grade']}；目的地：{result['trip_context'].get('destination') or '待补充'}。",
    ]
    lines.extend(f"{item['label']}：{item['formula']}。" for item in result["items"])
    if result["status"] == "complete":
        lines.append(f"合计：{result['total_amount']}元。")
    else:
        lines.append("完整合计暂不可用。")
        if result["missing_fields"]:
            lines.append(
                "请补充并确认：" + "、".join(MISSING[k] for k in result["missing_fields"]) + "。"
            )
        lines.extend(reason["message"] + "。" for reason in result["blocked_reasons"])
    return "\n".join(lines)


async def persist(
    db, user, conv, message, result, *, run_id=None, client_request_id=None, request_hash=None
):
    calc = Calculation(
        conversation_id=conv.id,
        message_id=message.id,
        actor_id=user.id,
        run_id=run_id,
        client_request_id=client_request_id,
        request_hash=request_hash,
        profile_snapshot=result["profile"],
        trip_snapshot=result["trip_context"],
        expense_snapshot=result.get("expense_context"),
        context_revision=result["context_revision"],
        status=result["status"],
        total_amount=Decimal(result["total_amount"])
        if result["total_amount"] is not None
        else None,
        missing_fields=result["missing_fields"],
        blocked_reasons=result["blocked_reasons"],
    )
    db.add(calc)
    await db.flush()
    for item in result["items"]:
        db.add(
            CalculationItem(
                calculation_id=calc.id,
                rule_id=uuid.UUID(item["rule_id"]),
                rule_revision=item["rule_revision"],
                kind=item["kind"],
                quantity=item["quantity"],
                unit_rate=Decimal(item["unit_rate"]),
                amount=Decimal(item["amount"]),
                rule_snapshot=item["rule_snapshot"],
            )
        )
    return calc


async def current_calculation(db, message_id):
    calc = await db.scalar(select(Calculation).where(Calculation.message_id == message_id))
    if calc is None:
        return True
    items = (
        await db.scalars(select(CalculationItem).where(CalculationItem.calculation_id == calc.id))
    ).all()
    for item in items:
        rule = await db.get(FeeRule, item.rule_id, populate_existing=True)
        if rule.status != "active" or rule.revision != item.rule_revision:
            return False
    return True


async def calculation_out(db, calc):
    items = (
        await db.scalars(
            select(CalculationItem)
            .where(CalculationItem.calculation_id == calc.id)
            .order_by(CalculationItem.kind.desc())
        )
    ).all()
    return {
        "id": str(calc.id),
        "status": calc.status,
        "currency": "CNY",
        "basis": "policy_standard_budget",
        "profile": calc.profile_snapshot,
        "trip_context": calc.trip_snapshot,
        "expense_context": calc.expense_snapshot,
        "total_amount": money(calc.total_amount) if calc.total_amount is not None else None,
        "missing_fields": calc.missing_fields,
        "blocked_reasons": calc.blocked_reasons,
        "items": [
            {
                "kind": i.kind,
                "label": LABELS[i.kind],
                "quantity": i.quantity,
                "unit_rate": money(i.unit_rate),
                "amount": money(i.amount),
                "unit": UNITS[i.kind],
                "formula": i.rule_snapshot.get("formula"),
                "rule_revision": i.rule_revision,
                "source": i.rule_snapshot.get("source"),
            }
            for i in items
        ],
    }


async def read_calculation(db, settings, user, calc_id):
    calc = await db.get(Calculation, calc_id)
    if calc is None:
        raise ApiError(404, "NOT_FOUND", "计算结果不存在或不可访问")
    await conversations.owned(db, user.id, calc.conversation_id)
    message = await db.get(Message, calc.message_id)
    visible, _, _ = await conversations.visibility(
        db, settings, user, calc.conversation_id, message
    )
    if not visible:
        raise ApiError(404, "NOT_FOUND", "计算依据或规则已变化，请重新计算")
    return await calculation_out(db, calc)


async def calculate(db, settings, actor, conversation_id, body, *, expense=False):
    conv = await conversations.owned(db, actor.user.id, conversation_id, lock=True)
    digest = hashlib.sha256(
        json.dumps(
            {
                "conversation_id": str(conv.id),
                **({"calculator": "expense"} if expense else {}),
                **body.model_dump(mode="json"),
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    existing = await db.scalar(
        select(Calculation).where(
            Calculation.actor_id == actor.user.id,
            Calculation.client_request_id == body.client_request_id,
        )
    )
    if existing:
        if existing.request_hash != digest:
            raise ApiError(409, "REQUEST_KEY_REUSED", "同一计算请求标识不能提交不同条件")
        return {
            "calculation": await read_calculation(db, settings, actor.user, existing.id),
            "conversation": await conversations.conversation_out(db, conv, actor.user),
        }
    profile = await db.get(EmployeeProfile, actor.user.id, populate_existing=True)
    if (
        conv.context_revision != body.context_revision
        or not profile
        or profile.revision != body.profile_revision
    ):
        raise ApiError(409, "CONTEXT_VERSION_CONFLICT", "员工资料或条件已更新，请刷新后确认")
    from app.models import AgentRun

    if await db.scalar(
        select(AgentRun.id).where(
            AgentRun.conversation_id == conv.id, AgentRun.status.in_(conversations.ACTIVE)
        )
    ):
        raise ApiError(409, "ACTIVE_RUN_EXISTS", "请先结束当前问答再确认计算")
    if expense:
        updated = body.expense_context.model_dump(mode="json", exclude_none=True)
        if updated != conv.expense_context:
            conv.expense_context = updated
            conv.context_revision += 1
    elif body.trip_context is not None:
        updated = body.trip_context.model_dump(exclude_none=True)
        if updated != conv.trip_context:
            conv.trip_context = updated
            conv.context_revision += 1
    if expense:
        from app.services.expenses import prepare as prepare_expense

        result, evidence = await prepare_expense(db, settings, actor.user, conv)
    else:
        result, evidence = await prepare(db, settings, actor.user, conv)
    submitted_at = now()
    db.add(
        Message(
            conversation_id=conv.id,
            role="user",
            content="确认费用条件并按制度规则核算参考额度。"
            if expense
            else "确认出差条件并按制度标准计算预算。",
            status="submitted",
            created_at=submitted_at,
        )
    )
    message = Message(
        conversation_id=conv.id,
        role="assistant",
        content=text_result(result),
        status="completed"
        if result["status"] == "complete"
        else "needs_clarification"
        if result["missing_fields"]
        else "insufficient_evidence",
        structured_result={
            "profile_revision": profile.revision,
            "missing_fields": result["missing_fields"],
        },
        created_at=submitted_at + timedelta(microseconds=1),
    )
    db.add(message)
    await db.flush()
    calc = await persist(
        db,
        actor.user,
        conv,
        message,
        result,
        client_request_id=body.client_request_id,
        request_hash=digest,
    )
    for index, item in enumerate(evidence.values(), 1):
        db.add(
            MessageSource(
                message_id=message.id,
                chunk_id=uuid.UUID(item["chunk_id"]),
                document_id=uuid.UUID(item["document_id"]),
                version_id=uuid.UUID(item["version_id"]),
                quote_snapshot=item["quote"],
                anchor_snapshot=item["anchor"],
                is_cited=True,
                citation_index=index,
            )
        )
    conv.updated_at = now()
    await db.commit()
    return {
        "calculation": await read_calculation(db, settings, actor.user, calc.id),
        "conversation": await conversations.conversation_out(db, conv, actor.user),
    }
