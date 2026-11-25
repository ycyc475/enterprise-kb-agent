"""Check saved acceptance evidence and current local demo state without model calls."""

import asyncio
import hashlib
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
    from app.core.config import get_settings
    from app.core.db import database

    settings = get_settings()
    engine, sessions = database(settings)
    try:
        async with sessions() as db:
            migration = await db.scalar(text("SELECT version_num FROM alembic_version"))
            counts = (
                (
                    await db.execute(
                        text(
                            "SELECT (SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' AND table_name<>'alembic_version') AS tables, (SELECT count(*) FROM documents WHERE deleted_at IS NULL) AS documents, (SELECT count(*) FROM agent_runs WHERE status IN ('queued','running')) AS active_runs, (SELECT count(*) FROM ingestion_jobs WHERE status IN ('queued','processing')) AS active_jobs, (SELECT count(*) FROM usage_records WHERE operation='m6_evaluation' AND status='reserved') AS evaluation_reservations"
                        )
                    )
                )
                .mappings()
                .one()
            )
            sources = (
                (
                    await db.execute(
                        text(
                            "SELECT d.title,v.sha256,v.version_no FROM documents d JOIN document_versions v ON v.id=d.current_version_id WHERE d.deleted_at IS NULL AND d.title LIKE '虚构演示制度-%'"
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
        credentials = json.loads((ROOT / "data/runtime/demo-credentials.json").read_text("utf-8"))
        with httpx.Client(
            base_url="http://127.0.0.1:8000",
            headers={"Origin": "http://127.0.0.1:5173"},
            timeout=10,
            trust_env=False,
        ) as client:
            response = client.post(
                "/api/v1/auth/login", json={"username": "admin", "password": credentials["admin"]}
            )
            assert response.status_code == 200
            client.headers["Authorization"] = "Bearer " + response.json()["data"]["access_token"]
            health = client.get("/health").json()
            rules = client.get("/api/v1/admin/fee-rules").json()["data"]["items"]
            usage = client.get("/api/v1/admin/usage").json()["data"]
            web = client.get("http://127.0.0.1:5173").status_code
            (ROOT / ".m6/openapi.json").write_text(
                json.dumps(client.get("/openapi.json").json(), ensure_ascii=False, indent=2),
                "utf-8",
            )
        backend = ET.parse(ROOT / ".m6/backend-results.xml").getroot().find("testsuite")
        deterministic = ET.parse(ROOT / ".m6/deterministic.xml").getroot().find("testsuite")
        fixed = [
            case
            for case in deterministic.findall("testcase")
            if case.attrib["classname"] == "tests.test_m6"
        ]
        qa = json.loads((ROOT / ".m6/qa-final.json").read_text("utf-8"))
        browser = json.loads((ROOT / ".m6/browser-results.json").read_text("utf-8"))["stats"]
        frozen = json.loads((ROOT / "data/evaluation/m6-manifest.json").read_text("utf-8"))
        expected_sources = {
            "虚构演示制度-" + title: frozen["materials"][code]["sha256"]
            for code, title in [
                ("POL-TRAVEL", "住宿标准"),
                ("POL-DAILY", "差旅补贴"),
                ("POL-PROCESS", "报销材料与办理流程"),
                ("POL-RD", "研发项目差旅归档要求"),
            ]
        }
        assert {row["title"]: row["sha256"] for row in sources} == expected_sources
        for entry in frozen["materials"].values():
            assert (
                hashlib.sha256((ROOT / entry["file"]).read_bytes()).hexdigest() == entry["sha256"]
            )
        assert (
            hashlib.sha256((ROOT / "data/evaluation/m6-cases.json").read_bytes()).hexdigest()
            == frozen["cases_sha256"]
        )
        assert (
            migration == "0005_m5"
            and counts["tables"] == 21
            and health["phase"] == "M6"
            and web == 200
        )
        assert (
            counts["documents"] == 7
            and counts["active_runs"]
            == counts["active_jobs"]
            == counts["evaluation_reservations"]
            == 0
        )
        assert (
            protected == 1
            and len(rules) == 5
            and all(r["effective_status"] == "active" for r in rules)
        )
        assert sorted(Decimal(r["unit_rate"]) for r in rules) == list(
            map(Decimal, ["80", "200", "300", "350", "500"])
        )
        assert (
            int(backend.attrib["tests"]) == 119
            and backend.attrib["failures"] == backend.attrib["errors"] == "0"
        )
        assert len(fixed) == 20 and all(not len(case) for case in fixed)
        assert len(qa["results"]) == 20 and all(
            row["source_review"]["passed"] for row in qa["results"]
        )
        assert (
            browser["expected"] == 14
            and browser["unexpected"] == browser["skipped"] == browser["flaky"] == 0
        )
        assert (ROOT / ".m6/demo.webm").stat().st_size > 10000
        assert not (ROOT / ".m5/membership-checkpoint.json").exists()
        recovery = json.loads((ROOT / ".m5/update-recovery.json").read_text("utf-8"))
        assert (
            recovery["passed"]
            and recovery["restored"] == "840.00"
            and recovery["after"] == "880.00"
        )
        assert (
            recovery["source_sha256_before"]
            == recovery["source_sha256_after"]
            == expected_sources["虚构演示制度-住宿标准"]
        )
        previous = Decimal(
            json.loads((ROOT / ".m5/final-summary.json").read_text("utf-8"))[
                "application_estimated_total_cny"
            ]
        )
        report = {
            "verified_at_utc": datetime.now(UTC).isoformat(),
            "health": health,
            "migration": migration,
            "state": dict(counts),
            "backend_tests": backend.attrib,
            "fixed_deterministic": 20,
            "qa_review_passed": 20,
            "qa_review_by": "development_agent_against_frozen_sources",
            "user_case_signoff": "not_recorded",
            "browser_tests": browser,
            "sources": [dict(row) for row in sources],
            "application_estimated_total_cny": usage["estimated_cost_cny"],
            "m6_estimated_increment_cny": str(Decimal(usage["estimated_cost_cny"]) - previous),
            "unknown_reserved_cny": usage["unknown_reserved_cny"],
            "final_20_question_evaluation_cny": qa["full_run_estimated_cost_cny"],
            "video": ".m6/demo.webm",
        }
        (ROOT / ".m6/final-summary.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    factory = asyncio.SelectorEventLoop if os.name == "nt" else asyncio.new_event_loop
    with asyncio.Runner(loop_factory=factory) as runner:
        runner.run(verify())
