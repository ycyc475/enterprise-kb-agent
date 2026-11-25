"""Run the explicit live evaluation in an isolated DB and retain its usage in the app ledger."""

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


async def ledger(evaluation_id, *, settle=False):
    from app.core.config import get_settings
    from app.core.db import database
    from app.models import UsageRecord

    settings = get_settings()
    engine, sessions = database(settings)
    try:
        async with sessions() as db:
            await db.execute(text("SELECT pg_advisory_xact_lock(8174204)"))
            if not settle:
                total = await db.scalar(
                    select(func.coalesce(func.sum(UsageRecord.estimated_cost), 0))
                )
                if total + Decimal(1) > settings.budget_limit_cny:
                    raise RuntimeError(
                        "Remaining application budget is below the 1 CNY evaluation reservation"
                    )
                db.add(
                    UsageRecord(
                        id=evaluation_id,
                        operation="m6_evaluation",
                        model=settings.chat_model,
                        status="reserved",
                        estimated_cost=Decimal(1),
                        price_snapshot={
                            "note": "isolated evaluation reserve; retain if interrupted",
                            "cap_cny": "1.00",
                        },
                    )
                )
            else:
                exported = json.loads((ROOT / ".m6/live-usage.json").read_text("utf-8"))
                if exported["evaluation_id"] != str(evaluation_id):
                    raise RuntimeError(
                        "Evaluation ledger identity mismatch; reservation retained"
                    )
                for row in exported["records"]:
                    row["id"] = uuid.UUID(row["id"])
                    row["estimated_cost"] = Decimal(row["estimated_cost"])
                    from datetime import datetime

                    row["created_at"] = datetime.fromisoformat(row["created_at"])
                    row["operation"] = "m6_" + row["operation"]
                    await db.execute(
                        insert(UsageRecord)
                        .values(**row)
                        .on_conflict_do_nothing(index_elements=["id"])
                    )
                reservation = await db.get(UsageRecord, evaluation_id)
                if reservation:
                    await db.delete(reservation)
            await db.commit()
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--cases", help="Optional pytest expression for an affected-case retest"
    )
    parser.add_argument("--suite", choices=["m6", "expenses"], default="m6")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required for paid evaluation calls")
    output = ROOT / ".m6"
    output.mkdir(exist_ok=True)
    evaluation_id = uuid.uuid4()
    factory = asyncio.SelectorEventLoop if os.name == "nt" else asyncio.new_event_loop
    with asyncio.Runner(loop_factory=factory) as runner:
        runner.run(ledger(evaluation_id))
    command = [
        sys.executable,
        "-m",
        "pytest",
        "tests/expenses_live.py" if args.suite == "expenses" else "tests/m6_live.py",
        "-q",
        "--tb=short",
        "--junitxml=../.m6/live-execution.xml",
    ]
    if args.cases:
        command += ["-k", args.cases]
    env = {
        **os.environ,
        "M6_LIVE_AUTHORIZED": "1",
        "M6_EVALUATION_ID": str(evaluation_id),
        "PYTHONIOENCODING": "utf-8",
    }
    print(
        "M6 live evaluation started; isolated DB, 1.00 CNY reserved in application budget.",
        flush=True,
    )
    result = subprocess.run(
        command,
        check=False,
        cwd=ROOT / "backend",
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    safe = re.sub(
        r"Bearer\s+[A-Za-z0-9._-]+", "Bearer [REDACTED]", result.stdout + result.stderr
    )
    (output / "live-execution.log").write_text(safe, "utf-8")
    archive = output / "evaluations" / str(evaluation_id)
    archive.mkdir(parents=True, exist_ok=True)
    qa_file = output / "qa-results.json"
    if qa_file.exists():
        from app.core.evaluation import summarize_expenses_live, summarize_m6_live

        qa_payload = json.loads(qa_file.read_text("utf-8"))
        metrics = (
            summarize_expenses_live(qa_payload)
            if args.suite == "expenses"
            else summarize_m6_live(qa_payload)
        )
        (output / "four-dim-metrics.json").write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2), "utf-8"
        )
    for name in [
        "qa-results.json",
        "four-dim-metrics.json",
        "live-usage.json",
        "live-execution.xml",
        "live-execution.log",
    ]:
        source = output / name
        if source.exists():
            shutil.copy2(source, archive / name)
    try:
        with asyncio.Runner(loop_factory=factory) as runner:
            runner.run(ledger(evaluation_id, settle=True))
        print(
            "Usage exported and imported into the application budget ledger.",
            flush=True,
        )
    except Exception as exc:
        print(
            f"Usage reconciliation needs attention ({type(exc).__name__}); conservative 1 CNY reservation retained.",
            flush=True,
        )
        raise
    print(safe, flush=True)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
