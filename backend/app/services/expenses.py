"""Deterministic capped calculations for a single explicitly confirmed expense class."""

from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select

from app.models import EmployeeProfile, FeeRule
from app.services import conversations, documents, fees
from app.services.auth import profile_out

KINDS = {"office": "办公用品采购", "training": "培训报销", "hospitality": "业务招待"}
FIELDS = {"quantity": "数量", "actual_amount": "实际申请金额"}


async def prepare(db, settings, user, conv):
    context = conv.expense_context
    profile = await db.get(EmployeeProfile, user.id, populate_existing=True)
    kb_ids = await conversations.scopes(db, conv.id)
    await conversations.validate_scopes(db, user, kb_ids)
    missing = [key for key in FIELDS if context.get(key) is None]
    rules = (
        await db.scalars(
            select(FeeRule)
            .where(FeeRule.kind == context["kind"], FeeRule.status == "active")
            .with_for_update()
        )
    ).all()
    candidates = []
    for rule in rules:
        row = await fees.source(db, settings, user, rule.source_chunk_id, kb_ids)
        if row and rule.source_quote in row[0].content:
            candidates.append((rule, row))
    reasons, items, evidence = [], [], {}
    if len(candidates) != 1:
        reasons.append(
            {
                "kind": context["kind"],
                "code": "RULE_CONFLICT" if candidates else "RULE_UNAVAILABLE",
                "message": "当前范围内有多条适用规则，请管理员核对"
                if candidates
                else "当前授权范围内没有已启用且来源有效的该类费用规则",
            }
        )
    elif not missing:
        rule, row = candidates[0]
        quantity = context["quantity"]
        if quantity > rule.max_quantity:
            reasons.append(
                {
                    "kind": rule.kind,
                    "code": "QUANTITY_LIMIT",
                    "message": f"该规则单次最多{rule.max_quantity}{fees.UNITS[rule.kind]}，超出范围需人工处理",
                }
            )
        else:
            actual = Decimal(str(context["actual_amount"]))
            scaled = actual * rule.reimbursement_ratio
            unit_cap = rule.unit_rate * quantity
            amount = min(scaled, unit_cap, rule.application_cap).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            formula = f"min(申请金额{fees.money(actual)}元 × 比例{rule.reimbursement_ratio}, 数量{quantity}{fees.UNITS[rule.kind]} × 单位上限{fees.money(rule.unit_rate)}元, 单次上限{fees.money(rule.application_cap)}元) = {fees.money(amount)}元"
            source = {
                "title": row[2].title,
                "version_no": row[1].version_no,
                "anchor": row[0].anchor,
                "quote": rule.source_quote,
            }
            items.append(
                {
                    "kind": rule.kind,
                    "label": fees.LABELS[rule.kind],
                    "quantity": quantity,
                    "unit": fees.UNITS[rule.kind],
                    "unit_rate": fees.money(rule.unit_rate),
                    "amount": fees.money(amount),
                    "formula": formula,
                    "rule_id": str(rule.id),
                    "rule_revision": rule.revision,
                    "rule_snapshot": {
                        "name": rule.name,
                        "unit_rate": fees.money(rule.unit_rate),
                        "application_cap": fees.money(rule.application_cap),
                        "reimbursement_ratio": str(rule.reimbursement_ratio),
                        "max_quantity": rule.max_quantity,
                        "actual_amount": fees.money(actual),
                        "formula": formula,
                        "source": source,
                    },
                }
            )
            evidence[str(row[0].id)] = {**documents.hit_out(*row), "quote": rule.source_quote[:800]}
    complete = not missing and not reasons and bool(items)
    return {
        "status": "complete" if complete else "needs_information" if missing else "blocked",
        "basis": "policy_expense_reference",
        "currency": "CNY",
        "profile": profile_out(profile),
        "trip_context": {},
        "expense_context": context,
        "context_revision": conv.context_revision,
        "items": items,
        "missing_fields": missing,
        "blocked_reasons": reasons,
        "total_amount": items[0]["amount"] if complete else None,
    }, evidence


def text_result(result):
    context = result["expense_context"]
    lines = [f"按虚构演示制度核算{KINDS[context['kind']]}参考额度；不代表实际审批或付款。"]
    if result["items"]:
        lines.extend(item["formula"] for item in result["items"])
    else:
        lines.append("参考额度暂不可用。")
    if result["missing_fields"]:
        lines.append(
            "请补充并确认：" + "、".join(FIELDS[key] for key in result["missing_fields"]) + "。"
        )
    lines.extend(item["message"] for item in result["blocked_reasons"])
    return "\n".join(lines)
