"""Curated management changes, committed atomically with the corresponding mutation."""

import json
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select

from app.models import AuditEvent

ACTIONS = {
    "rule.baseline": "迁移时的规则基线",
    "rule.created": "创建规则草稿",
    "rule.updated": "编辑规则并退回草稿",
    "rule.activated": "核对并启用规则",
    "rule.suspended": "手动暂停规则",
    "rule.source_invalidated": "来源变化，暂停规则",
    "rule.recovered": "核对新来源并恢复规则",
    "document.uploaded": "上传文档",
    "document.replace_requested": "提交替换版本",
    "document.processing_completed": "文档处理完成并发布",
    "document.processing_failed": "文档处理失败",
    "document.retry_requested": "重试文档处理",
    "document.deleted": "删除文档",
    "account.created": "创建账号",
    "account.updated": "更改账号角色或状态",
    "account.password_reset": "重置密码并撤销登录",
    "profile.updated": "修改员工资料",
    "knowledge_base.created": "创建知识库",
    "knowledge_base.updated": "修改知识库",
    "knowledge_base.members_updated": "修改知识库授权",
}
FIELDS = {
    "rule": {
        "name",
        "kind",
        "grade",
        "cities",
        "unit_rate",
        "application_cap",
        "reimbursement_ratio",
        "max_quantity",
        "source_chunk_id",
        "source_quote",
        "status",
        "revision",
        "confirmed_by",
        "confirmed_at",
        "suspension_reason",
    },
    "document": {
        "title",
        "kb_id",
        "current_version_id",
        "version_id",
        "version_no",
        "status",
        "format",
        "sha256",
        "effective_from",
        "effective_until",
        "job_id",
        "attempt_no",
        "error_code",
    },
    "account": {"username", "role", "is_active", "auth_version", "must_change_password"},
    "profile": {"display_name", "department", "grade", "revision"},
    "knowledge_base": {"name", "description", "is_active", "revision", "user_ids", "member_names"},
}


def snapshot(entity_type, value):
    if value is None:
        return None
    if not isinstance(value, dict):
        value = {key: getattr(value, key) for key in FIELDS[entity_type] if hasattr(value, key)}
    safe = {key: item for key, item in value.items() if key in FIELDS[entity_type]}
    if entity_type == "rule" and "unit_rate" in safe:
        safe["unit_rate"] = format(Decimal(safe["unit_rate"]), ".2f")
    return json.loads(
        json.dumps(
            safe,
            ensure_ascii=False,
            default=lambda v: v.isoformat() if isinstance(v, datetime) else str(v),
        )
    )


def record(db, entity_type, entity_id, action, actor=None, *, before=None, after=None):
    if action not in ACTIONS or entity_type not in FIELDS:
        raise ValueError("Unknown management event")
    user = getattr(actor, "user", actor)
    db.add(
        AuditEvent(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            actor_id=user.id if user else None,
            actor_label=user.username if user else "系统处理",
            before_snapshot=snapshot(entity_type, before),
            after_snapshot=snapshot(entity_type, after),
        )
    )


async def page(db, *, entity_type=None, entity_id=None, action=None, before_id=None, limit=50):
    query = select(AuditEvent)
    if entity_type:
        query = query.where(AuditEvent.entity_type == entity_type)
    if entity_id:
        query = query.where(AuditEvent.entity_id == entity_id)
    if action:
        query = query.where(AuditEvent.action == action)
    if before_id:
        query = query.where(AuditEvent.id < before_id)
    rows = list((await db.scalars(query.order_by(AuditEvent.id.desc()).limit(limit + 1))).all())
    more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": [
            {
                "id": row.id,
                "entity_type": row.entity_type,
                "entity_id": str(row.entity_id),
                "action": row.action,
                "action_label": ACTIONS.get(row.action, row.action),
                "actor_label": row.actor_label,
                "before": row.before_snapshot,
                "after": row.after_snapshot,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ],
        "next_before_id": rows[-1].id if more else None,
        "actions": ACTIONS,
    }
