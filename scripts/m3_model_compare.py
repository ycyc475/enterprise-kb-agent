"""Bounded live comparison through the actual LangChain agent; keys never enter reports."""
# ruff: noqa: E402 -- run from project root without installing the application package

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

from sqlalchemy import func, select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.chat_protocol import MessageSubmit
from app.core.config import get_settings
from app.main import create_app
from app.models import AgentRun, Message, UsageRecord
from app.services import auth, conversations
from app.services.chat_agent import execute_run


async def compare(args):
    credentials = json.loads((ROOT / "data/runtime/demo-credentials.json").read_text("utf-8"))
    cases = json.loads((ROOT / "data/evaluation/m3-model-cases.json").read_text("utf-8"))[
        : args.limit
    ]
    report = {
        "cases_file": "data/evaluation/m3-model-cases.json",
        "models": {},
        "formal_40_case_acceptance": False,
    }
    output = ROOT / ".m3/model-comparison.json"
    output.parent.mkdir(exist_ok=True)
    for model in args.models:
        app = create_app(
            get_settings().model_copy(update={"chat_model": model, "worker_enabled": False})
        )
        results = []
        try:
            async with app.state.sessions() as db:
                user, family, _ = await auth.login(
                    db, "employee_li", credentials["employee_li"], app.state.settings
                )
                context = auth.AuthContext(user, family, user.auth_version)
                bases = await auth.allowed_kbs(db, user)
                kb = next(k for k in bases if k.name == "企业公共制度")
                kb_id = kb.id
            for case in cases:
                async with app.state.sessions() as db:
                    conv = await conversations.create(
                        db, user, "M3对照-" + model + "-" + case["id"], [kb_id]
                    )
                    run_data = await conversations.submit(
                        app,
                        db,
                        context,
                        uuid.UUID(conv["id"]),
                        MessageSubmit(
                            content=case["question"],
                            client_request_id=uuid.uuid4(),
                            context_revision=1,
                        ),
                    )
                begin = time.monotonic()
                try:
                    await execute_run(app, uuid.UUID(run_data["id"]))
                    async with app.state.sessions() as db:
                        run = await db.get(AgentRun, uuid.UUID(run_data["id"]))
                        message = (
                            await db.get(Message, run.response_message_id)
                            if run.response_message_id
                            else None
                        )
                        value = (
                            await conversations.message_out(db, app.state.settings, user, message)
                            if message
                            else {"content": None, "citations": []}
                        )
                        cost = await db.scalar(
                            select(func.coalesce(func.sum(UsageRecord.estimated_cost), 0)).where(
                                UsageRecord.run_id == run.id
                            )
                        )
                        kind, answer = case["kind"], value.get("content") or ""
                        if kind in {"evidence", "cross_document"}:
                            passed = (
                                run.status == "completed"
                                and bool(value["citations"])
                                and all(word in answer for word in case["contains"])
                            )
                            if kind == "cross_document":
                                passed = (
                                    passed and len({c["title"] for c in value["citations"]}) >= 2
                                )
                        elif kind == "profile":
                            passed = (
                                run.status == "completed"
                                and "S1" in answer
                                and not value["citations"]
                            )
                        elif kind == "clarification":
                            passed = (
                                run.status == "needs_clarification"
                                and "destination" in value.get("missing_fields", [])
                            )
                        else:
                            passed = run.status == "insufficient_evidence" or (
                                run.status == "completed"
                                and "深圳" in answer
                                and any(
                                    s in answer for s in ["没有", "未", "不明确", "不足", "不包含"]
                                )
                            )
                        row = {
                            "id": case["id"],
                            "question": case["question"],
                            "kind": kind,
                            "passed_checks": bool(passed),
                            "status": run.status,
                            "stop_reason": run.stop_reason,
                            "answer": answer,
                            "citations": value["citations"],
                            "model_attempts": run.model_attempts,
                            "search_attempts": run.search_attempts,
                            "estimated_cost_cny": str(cost),
                            "elapsed_seconds": round(time.monotonic() - begin, 3),
                        }
                        results.append(row)
                        print(
                            json.dumps(
                                {
                                    "model": model,
                                    "case": case["id"],
                                    "passed_checks": bool(passed),
                                    "status": run.status,
                                    "reason": run.stop_reason,
                                },
                                ensure_ascii=False,
                            ),
                            flush=True,
                        )
                finally:
                    async with app.state.sessions() as db:
                        await conversations.remove(db, user, uuid.UUID(conv["id"]))
                report["models"][model] = {
                    "results": results,
                    "passed_checks": sum(r["passed_checks"] for r in results),
                    "count": len(results),
                }
                output.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
        finally:
            await app.state.engine.dispose()
    print(
        json.dumps(
            {
                "report": ".m3/model-comparison.json",
                "models": {
                    m: {"passed": v["passed_checks"], "count": v["count"]}
                    for m, v in report["models"].items()
                },
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--limit", type=int, default=10, choices=range(1, 11))
    parser.add_argument(
        "--models",
        nargs="+",
        choices=["qwen3.7-flash", "qwen3.7-plus"],
        default=["qwen3.7-flash", "qwen3.7-plus"],
    )
    args = parser.parse_args()
    if not args.live:
        parser.error("Explicit --live is required for paid model calls")
    factory = asyncio.SelectorEventLoop if os.name == "nt" else asyncio.new_event_loop
    with asyncio.Runner(loop_factory=factory) as runner:
        runner.run(compare(args))
