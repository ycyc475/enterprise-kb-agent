import asyncio
import hashlib
import math
from decimal import Decimal

import httpx
from sqlalchemy import func, select, text

from app.core.errors import ApiError
from app.models import UsageRecord

PRICE = Decimal("0.5") / 1_000_000  # Beijing text-embedding-v4, CNY/input token.


def space(settings):
    return hashlib.sha256(
        f"{settings.dashscope_base_url.rstrip('/')}|{settings.embedding_model}|{settings.embedding_dimensions}|{settings.embedding_revision}".encode()
    ).hexdigest()


async def usage_summary(sessions, settings):
    async with sessions() as db:
        total = await db.scalar(select(func.coalesce(func.sum(UsageRecord.estimated_cost), 0)))
        count = await db.scalar(select(func.count()).select_from(UsageRecord))
        rows = (
            (
                await db.execute(
                    select(
                        UsageRecord.model,
                        UsageRecord.operation,
                        UsageRecord.status,
                        func.count().label("requests"),
                        func.coalesce(func.sum(UsageRecord.input_tokens), 0).label("input_tokens"),
                        func.coalesce(func.sum(UsageRecord.output_tokens), 0).label(
                            "output_tokens"
                        ),
                        func.sum(UsageRecord.estimated_cost).label("cost"),
                    )
                    .group_by(UsageRecord.model, UsageRecord.operation, UsageRecord.status)
                    .order_by(UsageRecord.model, UsageRecord.operation, UsageRecord.status)
                )
            )
            .mappings()
            .all()
        )
        unknown = sum(r["cost"] for r in rows if r["status"] != "estimated")
    return {
        "estimated_cost_cny": str(total),
        "requests": count,
        "warning": total >= settings.budget_warning_cny,
        "paused": total >= settings.budget_limit_cny,
        "warning_cny": str(settings.budget_warning_cny),
        "limit_cny": str(settings.budget_limit_cny),
        "price_cny_per_million": "0.5",
        "unknown_reserved_cny": str(unknown),
        "remaining_estimated_cny": str(max(Decimal("0"), settings.budget_limit_cny - total)),
        "breakdown": [
            {
                "model": r["model"],
                "operation": r["operation"],
                "status": r["status"],
                "requests": r["requests"],
                "input_tokens": r["input_tokens"],
                "output_tokens": r["output_tokens"],
                "estimated_cost_cny": str(r["cost"]),
            }
            for r in rows
        ],
        "note": "本应用模型调用估算，含对话、向量和失败/在途预留；实际账单以百炼为准",
    }


class Embedder:
    def __init__(self, settings, sessions):
        self.settings, self.sessions = settings, sessions

    async def embed(
        self, texts: list[str], *, actor_id=None, operation="document_embedding", run_id=None
    ):
        cfg = self.settings
        if not cfg.dashscope_api_key.get_secret_value() or not cfg.dashscope_base_url:
            raise ApiError(503, "MODEL_NOT_CONFIGURED", "请先在本机配置百炼Key和接入地址")
        result = []
        for start in range(0, len(texts), 10):
            batch = texts[start : start + 10]
            reserve = Decimal(sum(len(t.encode("utf-8")) for t in batch)) * PRICE
            async with self.sessions() as db:
                await db.execute(text("SELECT pg_advisory_xact_lock(8174204)"))
                total = await db.scalar(
                    select(func.coalesce(func.sum(UsageRecord.estimated_cost), 0))
                )
                if total + reserve > cfg.budget_limit_cny:
                    raise ApiError(429, "BUDGET_PAUSED", "已达到应用预算上限，暂停新增向量调用")
                record = UsageRecord(
                    run_id=run_id,
                    actor_id=actor_id,
                    operation=operation,
                    model=cfg.embedding_model,
                    status="reserved",
                    estimated_cost=reserve,
                )
                db.add(record)
                await db.commit()
                record_id = record.id
            try:
                async with httpx.AsyncClient(timeout=25, trust_env=False) as client:
                    response = await client.post(
                        cfg.dashscope_base_url.rstrip("/") + "/embeddings",
                        headers={
                            "Authorization": "Bearer " + cfg.dashscope_api_key.get_secret_value()
                        },
                        json={
                            "model": cfg.embedding_model,
                            "input": batch,
                            "dimensions": cfg.embedding_dimensions,
                            "encoding_format": "float",
                        },
                    )
                if response.status_code != 200:
                    raise ApiError(
                        502,
                        "EMBEDDING_UNAVAILABLE",
                        f"向量服务请求失败（HTTP {response.status_code}），请检查账号权限、额度或稍后重试",
                    )
                body = response.json()
                rows = sorted(body["data"], key=lambda x: x["index"])
                vectors = [r["embedding"] for r in rows]
                if [r["index"] for r in rows] != list(range(len(batch))) or any(
                    len(v) != 1024
                    or not all(isinstance(n, (int, float)) and math.isfinite(n) for n in v)
                    or sum(n * n for n in v) == 0
                    for v in vectors
                ):
                    raise ValueError("invalid vectors")
                tokens = body.get("usage", {}).get("total_tokens")
                if not isinstance(tokens, int) or tokens < 0:
                    tokens = None
                async with self.sessions() as db:
                    record = await db.get(UsageRecord, record_id)
                    record.status = "estimated" if tokens is not None else "usage_unknown"
                    record.input_tokens = tokens
                    if tokens is not None:
                        record.estimated_cost = Decimal(tokens) * PRICE
                    record.provider_request_id = str(body.get("id", ""))[:128] or None
                    await db.commit()
                result.extend(vectors)
            except (Exception, asyncio.CancelledError) as exc:
                async with self.sessions() as db:
                    record = await db.get(UsageRecord, record_id)
                    record.status = "failed_unknown"
                    await db.commit()
                if isinstance(exc, (ApiError, asyncio.CancelledError)):
                    raise
                raise ApiError(
                    502, "EMBEDDING_UNAVAILABLE", "向量服务超时或返回格式不正确，请稍后重试"
                ) from None
        return result
