"""Verify current M5 state and saved validation evidence without model calls."""

import asyncio
import json
import os
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


async def verify():
    from app.core.config import Settings
    from app.core.db import database

    settings = Settings()
    engine, sessions = database(settings)
    try:
        async with sessions() as db:
            migration = await db.scalar(text("SELECT version_num FROM alembic_version"))
            tables = await db.scalar(
                text(
                    "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' AND table_name<>'alembic_version'"
                )
            )
            active_runs = await db.scalar(
                text("SELECT count(*) FROM agent_runs WHERE status IN ('queued','running')")
            )
            active_documents = await db.scalar(
                text("SELECT count(*) FROM documents WHERE deleted_at IS NULL")
            )
            event_counts = (
                (
                    await db.execute(
                        text(
                            "SELECT action,count(*) AS records FROM audit_events GROUP BY action ORDER BY action"
                        )
                    )
                )
                .mappings()
                .all()
            )
            protected = await db.scalar(
                text(
                    "SELECT count(*) FROM pg_trigger WHERE tgname='audit_append_only' AND tgenabled='O'"
                )
            )
            source_hash = await db.scalar(
                text(
                    "SELECT v.sha256 FROM documents d JOIN document_versions v ON v.id=d.current_version_id WHERE d.title='虚构演示制度-住宿标准' AND d.deleted_at IS NULL"
                )
            )
        credentials = json.loads((ROOT / "data/runtime/demo-credentials.json").read_text("utf-8"))
        with httpx.Client(
            base_url="http://127.0.0.1:8000",
            headers={"Origin": "http://127.0.0.1:5173"},
            timeout=10,
            trust_env=False,
        ) as client:
            health = client.get("/health").json()
            schema = client.get("/openapi.json")
            schema.raise_for_status()
            (ROOT / ".m5/openapi.json").write_text(
                json.dumps(schema.json(), ensure_ascii=False, indent=2), "utf-8"
            )
            response = client.post(
                "/api/v1/auth/login", json={"username": "admin", "password": credentials["admin"]}
            )
            response.raise_for_status()
            client.headers["Authorization"] = "Bearer " + response.json()["data"]["access_token"]
            rules = client.get("/api/v1/admin/fee-rules").json()["data"]["items"]
            usage = client.get("/api/v1/admin/usage").json()["data"]
        with httpx.Client(timeout=10, trust_env=False) as client:
            web_status = client.get("http://127.0.0.1:5173").status_code
        backend = ET.parse(ROOT / ".m5/backend-results.xml").getroot().find("testsuite")
        browser = json.loads((ROOT / ".m5/browser-results.json").read_text("utf-8"))["stats"]
        recovery = json.loads((ROOT / ".m5/update-recovery.json").read_text("utf-8"))
        previous = Decimal(
            json.loads((ROOT / ".m4/final-summary.json").read_text("utf-8"))[
                "application_estimated_total_cny"
            ]
        )
        report = {
            "verified_at_utc": datetime.now(UTC).isoformat(),
            "health": health,
            "web_status": web_status,
            "migration": migration,
            "business_tables": tables,
            "active_runs": active_runs,
            "active_documents": active_documents,
            "active_rules": sum(r["effective_status"] == "active" for r in rules),
            "audit_events": [dict(row) for row in event_counts],
            "history_mutation_guard_present": protected == 1,
            "backend_tests": dict(backend.attrib),
            "browser_tests": browser,
            "update_recovery": recovery,
            "restored_policy_sha256": source_hash,
            "application_estimated_total_cny": usage["estimated_cost_cny"],
            "m5_estimated_increment_cny": str(Decimal(usage["estimated_cost_cny"]) - previous),
            "unknown_reserved_cny": usage["unknown_reserved_cny"],
            "note": "M5 stage verification. Successful management events only; earlier rule state is labeled baseline. Final fixed 40-case acceptance remains M6.",
        }
        assert (
            migration == "0005_m5"
            and tables == 21
            and health["phase"] == "M5"
            and web_status == 200
        )
        assert (
            active_runs == 0
            and active_documents == 5
            and len(rules) == 5
            and report["active_rules"] == 5
        )
        assert all(
            Decimal(r["unit_rate"])
            == {
                "演示-S1-北京上海住宿": Decimal("300"),
                "演示-S2-北京上海住宿": Decimal("500"),
                "演示-S1-杭州南京住宿": Decimal("200"),
                "演示-S2-杭州南京住宿": Decimal("350"),
                "演示-每日补贴": Decimal("80"),
            }[r["name"]]
            for r in rules
        )
        assert protected == 1 and any(
            row["action"] == "rule.baseline" and row["records"] == 5 for row in event_counts
        )
        assert (
            int(backend.attrib["tests"]) >= 93
            and backend.attrib["failures"] == "0"
            and backend.attrib["errors"] == "0"
        )
        assert browser["expected"] == 13 and browser["unexpected"] == 0 and browser["skipped"] == 0
        assert (
            recovery["passed"]
            and recovery["before"] == "840.00"
            and recovery["after"] == "880.00"
            and recovery["restored"] == "840.00"
        )
        assert source_hash == recovery["source_sha256_before"] == recovery["source_sha256_after"]
        (ROOT / ".m5/final-summary.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str), "utf-8"
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    factory = asyncio.SelectorEventLoop if os.name == "nt" else asyncio.new_event_loop
    with asyncio.Runner(loop_factory=factory) as runner:
        runner.run(verify())
