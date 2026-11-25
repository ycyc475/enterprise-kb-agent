"""Read-only local M3 verification. Does not call model APIs or print secrets."""

import asyncio
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


async def verify():
    from app.core.config import Settings
    from app.core.db import database
    from app.services.chat_models import QwenProvider

    settings = Settings()
    engine, sessions = database(settings)
    try:
        async with sessions() as db:
            migration = await db.scalar(text("SELECT version_num FROM alembic_version"))
            tables = await db.scalar(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_type='BASE TABLE' "
                    "AND table_name <> 'alembic_version'"
                )
            )
            active_runs = await db.scalar(
                text("SELECT count(*) FROM agent_runs WHERE status IN ('queued','running')")
            )
            active_documents = await db.scalar(
                text("SELECT count(*) FROM documents WHERE deleted_at IS NULL")
            )
            usage = (
                (
                    await db.execute(
                        text(
                            "SELECT model, operation, status, count(*) AS requests, "
                            "coalesce(sum(input_tokens),0) AS input_tokens, "
                            "coalesce(sum(output_tokens),0) AS output_tokens, "
                            "sum(estimated_cost) AS estimated_cost_cny "
                            "FROM usage_records WHERE run_id IS NOT NULL "
                            "GROUP BY model, operation, status ORDER BY model, operation, status"
                        )
                    )
                )
                .mappings()
                .all()
            )
            total = await db.scalar(
                text("SELECT coalesce(sum(estimated_cost),0) FROM usage_records")
            )
            stage_total = await db.scalar(
                text(
                    "SELECT coalesce(sum(estimated_cost),0) FROM usage_records WHERE run_id IS NOT NULL"
                )
            )
        with httpx.Client(base_url="http://127.0.0.1:8000", timeout=10) as client:
            response = client.get("/health")
            response.raise_for_status()
            health = response.json()
            response = client.get("/openapi.json")
            response.raise_for_status()
            (ROOT / ".m3/openapi.json").write_text(
                json.dumps(response.json(), ensure_ascii=False, indent=2), "utf-8"
            )
        with httpx.Client(timeout=10) as client:
            response = client.get("http://127.0.0.1:5173")
            response.raise_for_status()
        backend = ET.parse(ROOT / ".m3/backend-results.xml").getroot()
        suite = backend if backend.tag == "testsuite" else backend.find("testsuite")
        browser = json.loads((ROOT / ".m3/browser-results.json").read_text("utf-8"))["stats"]
        report = {
            "health": health,
            "web_status": response.status_code,
            "migration": migration,
            "business_tables": tables,
            "chat_ready": QwenProvider(settings).ready,
            "default_model": settings.chat_model,
            "active_runs": active_runs,
            "active_documents": active_documents,
            "backend_tests": dict(suite.attrib),
            "browser_tests": browser,
            "model_selection": json.loads((ROOT / ".m3/model-selection.json").read_text("utf-8")),
            "m3_run_usage": [dict(row) for row in usage],
            "m3_run_estimated_cost_cny": str(stage_total),
            "application_estimated_total_cny": str(total),
            "budget_warning_cny": str(settings.budget_warning_cny),
            "budget_limit_cny": str(settings.budget_limit_cny),
            "note": "Application estimates include unknown/in-flight reservations; not a provider invoice. "
            "M3 usage includes initial experiments and browser verification. Final 40-case acceptance pending.",
        }
        assert health["phase"] == "M3" and health["database"] == "ok"
        assert migration == "0003_m3" and tables == 17 and active_runs == 0
        assert report["chat_ready"] and report["default_model"] == "qwen3.7-flash"
        assert int(suite.attrib["failures"]) == 0 and int(suite.attrib["errors"]) == 0
        assert browser["expected"] == 8 and browser["unexpected"] == 0 and browser["skipped"] == 0
        (ROOT / ".m3/final-summary.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str), "utf-8"
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    factory = asyncio.SelectorEventLoop if os.name == "nt" else asyncio.new_event_loop
    with asyncio.Runner(loop_factory=factory) as runner:
        runner.run(verify())
