"""Qwen/LangChain adapter and shared estimated-cost reservations; never log provider bodies."""

import json
from decimal import Decimal

from langchain_openai import ChatOpenAI
from sqlalchemy import func, select, text

from app.core.errors import ApiError
from app.models import UsageRecord

RATES = {
    "qwen3.7-flash": (Decimal("0.2"), Decimal("0.8")),
    "qwen3.7-plus": (Decimal("2"), Decimal("8")),
}


class QwenProvider:
    def __init__(self, settings):
        self.settings = settings

    @property
    def ready(self):
        return bool(
            self.settings.dashscope_api_key.get_secret_value()
            and self.settings.dashscope_base_url
            and self.settings.chat_model in RATES
        )

    def create(self, model):
        cfg = self.settings
        if not self.ready or model not in RATES:
            raise ApiError(503, "CHAT_NOT_CONFIGURED", "对话服务未配置或当前型号不受支持")
        return ChatOpenAI(
            model=model,
            api_key=cfg.dashscope_api_key.get_secret_value(),
            base_url=cfg.dashscope_base_url,
            max_retries=0,
            timeout=25,
            max_tokens=cfg.chat_max_output_tokens,
            temperature=0,
            extra_body={"enable_thinking": False},
        )


async def reserve(
    sessions, settings, model, payload, *, actor_id=None, run_id=None, operation="chat"
):
    if model not in RATES:
        raise ApiError(503, "UNPRICED_MODEL", "请先为该对话模型配置已核对的价格")
    input_rate, output_rate = RATES[model]
    upper = len(json.dumps(payload, ensure_ascii=False, default=str).encode()) + 1024
    # Keep short-input pricing valid and leave space for final answers/tool schemas.
    if upper > 30_000:
        raise ApiError(422, "CONTEXT_TOO_LONG", "本轮上下文过长，请缩短问题或新建会话")
    amount = (
        Decimal(upper) * input_rate + Decimal(settings.chat_max_output_tokens) * output_rate
    ) / 1_000_000
    async with sessions() as db:
        await db.execute(text("SELECT pg_advisory_xact_lock(8174204)"))
        total = await db.scalar(select(func.coalesce(func.sum(UsageRecord.estimated_cost), 0)))
        if total + amount > settings.budget_limit_cny:
            raise ApiError(429, "BUDGET_PAUSED", "已达到应用预算上限，暂停新增模型请求")
        row = UsageRecord(
            actor_id=actor_id,
            run_id=run_id,
            operation=operation,
            model=model,
            status="reserved",
            estimated_cost=amount,
            price_snapshot={
                "input_cny_per_million": str(input_rate),
                "output_cny_per_million": str(output_rate),
                "thinking": False,
                "region": "cn-beijing",
                "checked_on": "2026-11-04",
            },
        )
        db.add(row)
        await db.commit()
        return row.id


async def settle(sessions, record_id, message=None):
    async with sessions() as db:
        row = await db.get(UsageRecord, record_id)
        row.status = "failed_unknown"
        if message is not None:
            usage = message.usage_metadata or {}
            inputs, outputs = usage.get("input_tokens"), usage.get("output_tokens")
            row.status = "usage_unknown"
            if (
                isinstance(inputs, int)
                and isinstance(outputs, int)
                and inputs >= 0
                and outputs >= 0
            ):
                row.input_tokens, row.output_tokens, row.status = inputs, outputs, "estimated"
                prices = row.price_snapshot
                row.estimated_cost = (
                    Decimal(inputs) * Decimal(prices["input_cny_per_million"])
                    + Decimal(outputs) * Decimal(prices["output_cny_per_million"])
                ) / 1_000_000
            row.provider_request_id = (
                str(message.response_metadata.get("request_id", message.id or ""))[:128] or None
            )
        await db.commit()
