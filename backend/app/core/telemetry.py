"""Structured JSON logging, trace context propagation, and safe span telemetry."""

from __future__ import annotations

import json
import logging
import sys
import time
from contextvars import ContextVar
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)
run_id_var: ContextVar[str | None] = ContextVar("run_id", default=None)

SENSITIVE_KEYS = frozenset(
    {
        "password",
        "password_hash",
        "token",
        "access_token",
        "refresh_token",
        "secret",
        "api_key",
        "dashscope_api_key",
        "authorization",
        "cookie",
        "content",
        "query",
        "quote",
        "prompt",
        "system_prompt",
        "body",
        "answer",
        "messages",
        "args",
    }
)

VALIDATION_MARKERS = (
    "不符或存在歧义",
    "不能自行补齐",
    "条件不合法",
    "费用类型不明确",
    "费用条件不合法",
    "实际申请金额格式不合法",
    "一次确认一种费用",
    "不能执行差旅预算",
    "检索问题应为1至300个字符",
)


def ms_since(start: float) -> int:
    return max(0, int(round((time.perf_counter() - start) * 1000)))


def _sanitize(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {
            str(k): _sanitize(v)
            for k, v in value.items()
            if str(k).lower() not in SENSITIVE_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    return str(value)


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", record.getMessage()),
        }
        trace_id = getattr(record, "trace_id", None) or trace_id_var.get()
        run_id = getattr(record, "run_id", None) or run_id_var.get()
        if trace_id:
            payload["trace_id"] = str(trace_id)
        if run_id:
            payload["run_id"] = str(run_id)
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(_sanitize(fields))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("app")
    logger.setLevel(level)
    if not any(getattr(h, "_enterprise_kb_json", False) for h in logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonLogFormatter())
        handler._enterprise_kb_json = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    return logger


def log_event(event: str, *, level: int = logging.INFO, logger_name: str = "app.telemetry", **fields: Any) -> None:
    logger = logging.getLogger(logger_name)
    logger.log(
        level,
        event,
        extra={
            "event": event,
            "trace_id": fields.pop("trace_id", None) or trace_id_var.get(),
            "run_id": fields.pop("run_id", None) or run_id_var.get(),
            "fields": _sanitize(fields),
        },
    )


def extract_token_usage(message: Any) -> dict[str, int]:
    usage = getattr(message, "usage_metadata", None) or {}
    result: dict[str, int] = {}
    for key in ("input_tokens", "output_tokens"):
        val = usage.get(key)
        if isinstance(val, int) and val >= 0:
            result[key] = val
    return result


def classify_tool_outcome(tool_name: str, result: Any) -> str:
    """Classify tool output into safe operational status without copying user/doc text."""
    text = getattr(result, "content", result)
    if not isinstance(text, str):
        text = str(text or "")
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            data = json.loads(stripped)
            if isinstance(data, dict):
                if data.get("error"):
                    return "validation_rejected"
                if data.get("status") == "needs_confirmation":
                    return "needs_confirmation"
                notice = str(data.get("notice") or "")
                if "已停止" in notice or "额度已用完" in notice:
                    return "search_stopped"
                if "已检索，未新增调用" in notice:
                    return "cached"
                return "ok"
        except json.JSONDecodeError:
            pass
    if any(marker in stripped for marker in VALIDATION_MARKERS):
        return "validation_rejected"
    if tool_name == "Answer" and "结构化结果格式不正确" in stripped:
        return "validation_rejected"
    return "ok"


def summarize_trace(run: Any, events: list[Any], usage_rows: list[Any]) -> dict[str, Any]:
    """Aggregate safe per-stage latency, tool call telemetry, and token/cost totals."""
    stage_durations: dict[str, int] = {}
    stage_counts: dict[str, int] = {}
    tool_counts: dict[str, int] = {}
    tool_outcomes: dict[str, int] = {}
    total_duration_ms: int | None = None
    spans = []

    for row in events:
        meta = row.meta if isinstance(row.meta, dict) else {}
        stage = meta.get("stage")
        duration = row.duration_ms
        if stage == "finish" and isinstance(duration, int):
            total_duration_ms = duration
        elif isinstance(stage, str):
            stage_counts[stage] = stage_counts.get(stage, 0) + 1
            if isinstance(duration, int):
                stage_durations[stage] = stage_durations.get(stage, 0) + duration
        if stage == "tool":
            name = str(meta.get("tool_name") or "unknown")
            outcome = str(meta.get("outcome") or "ok")
            tool_counts[name] = tool_counts.get(name, 0) + 1
            tool_outcomes[outcome] = tool_outcomes.get(outcome, 0) + 1
        spans.append(
            {
                "seq": row.seq,
                "type": row.event_type,
                "label": row.label,
                "duration_ms": row.duration_ms,
                "meta": meta or None,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )

    if total_duration_ms is None and events:
        first_ts = events[0].created_at
        last_ts = events[-1].created_at
        if first_ts and last_ts:
            total_duration_ms = max(0, int(round((last_ts - first_ts).total_seconds() * 1000)))

    input_tokens = sum(r.input_tokens or 0 for r in usage_rows)
    output_tokens = sum(r.output_tokens or 0 for r in usage_rows)
    estimated_cost = sum((r.estimated_cost for r in usage_rows), Decimal("0"))

    return {
        "run_id": str(run.id),
        "trace_id": str(run.id),
        "conversation_id": str(run.conversation_id),
        "status": run.status,
        "stop_reason": run.stop_reason,
        "total_duration_ms": total_duration_ms,
        "attempts": {
            "search": run.search_attempts,
            "model": run.model_attempts,
            "tool": run.tool_attempts,
        },
        "stage_durations_ms": stage_durations,
        "stage_counts": stage_counts,
        "tool_summary": {
            "total": sum(tool_counts.values()),
            "by_tool": tool_counts,
            "by_outcome": tool_outcomes,
        },
        "token_usage": {
            "requests": len(usage_rows),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "estimated_cost_cny": str(estimated_cost),
        },
        "spans": spans,
    }
