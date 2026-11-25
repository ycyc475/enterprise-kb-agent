"""Read-only final checks plus isolated local calculator examples; no model requests."""

import asyncio
import json
import os
import sys
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def check(response, status=200):
    if response.status_code != status:
        raise RuntimeError(f"Unexpected HTTP {response.status_code} at {response.request.url.path}")
    return response.json()["data"] if status != 204 else None


def calculator_examples():
    credentials = json.loads((ROOT / "data/runtime/demo-credentials.json").read_text("utf-8"))
    cases = [
        ("C01", "employee_li", "北京", 3, 2, "840.00"),
        ("C02", "employee_wang", "北京", 3, 2, "1240.00"),
        ("C03", "employee_li", "杭州", 3, 2, "640.00"),
        ("C04", "employee_wang", "南京", 2, 1, "510.00"),
        ("C05", "employee_li", "上海", 1, 0, "80.00"),
        ("C06", "employee_li", "北京", 30, 29, "11100.00"),
        ("C07", "employee_li", "北京", 3, -1, None),
        ("C08", "employee_li", "广州", 3, 2, None),
    ]
    results = []
    with httpx.Client(
        base_url="http://127.0.0.1:8000/api/v1",
        headers={"Origin": "http://127.0.0.1:5173"},
        trust_env=False,
        timeout=15,
    ) as client:
        for case_id, username, city, days, nights, expected in cases:
            identity = check(
                client.post(
                    "/auth/login", json={"username": username, "password": credentials[username]}
                )
            )
            headers = {"Authorization": "Bearer " + identity["access_token"]}
            kb = next(k for k in identity["user"]["knowledge_bases"] if k["name"] == "企业公共制度")
            conv = check(
                client.post(
                    "/conversations",
                    headers=headers,
                    json={"title": "M4验收临时用例-" + case_id, "knowledge_base_ids": [kb["id"]]},
                ),
                201,
            )
            try:
                response = client.post(
                    f"/conversations/{conv['id']}/calculations",
                    headers=headers,
                    json={
                        "client_request_id": str(uuid.uuid4()),
                        "context_revision": conv["context_revision"],
                        "profile_revision": identity["user"]["profile"]["revision"],
                        "trip_context": {
                            "destination": city,
                            "travel_days": days,
                            "lodging_nights": nights,
                        },
                    },
                )
                if case_id == "C07":
                    assert response.status_code == 422
                    result = {"id": case_id, "status": "validation_rejected", "passed": True}
                else:
                    calc = check(response, 201)["calculation"]
                    assert calc["total_amount"] == expected
                    assert calc["status"] == ("blocked" if expected is None else "complete")
                    result = {
                        "id": case_id,
                        "username": username,
                        "trip": calc["trip_context"],
                        "status": calc["status"],
                        "expected_total": expected,
                        "actual_total": calc["total_amount"],
                        "items": calc["items"],
                        "passed": True,
                    }
                results.append(result)
            finally:
                check(client.delete(f"/conversations/{conv['id']}", headers=headers), 204)
    (ROOT / ".m4/live-calculations.json").write_text(
        json.dumps({"model_calls": 0, "cases": results}, ensure_ascii=False, indent=2), "utf-8"
    )
    return results


async def verify(examples):
    from app.core.config import Settings
    from app.core.db import database

    settings = Settings()
    engine, sessions = database(settings)
    try:
        async with sessions() as db:
            migration = await db.scalar(text("SELECT version_num FROM alembic_version"))
            tables = await db.scalar(
                text(
                    "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' AND table_name <> 'alembic_version'"
                )
            )
            runs = await db.scalar(
                text("SELECT count(*) FROM agent_runs WHERE status IN ('queued','running')")
            )
            documents = await db.scalar(
                text("SELECT count(*) FROM documents WHERE deleted_at IS NULL")
            )
            total = await db.scalar(
                text("SELECT coalesce(sum(estimated_cost),0) FROM usage_records")
            )
            rules = (
                (
                    await db.execute(
                        text(
                            "SELECT name,kind,grade,cities,unit_rate,status,revision FROM fee_rules ORDER BY name"
                        )
                    )
                )
                .mappings()
                .all()
            )
        with httpx.Client(base_url="http://127.0.0.1:8000", timeout=10, trust_env=False) as client:
            health = client.get("/health").json()
            response = client.get("/openapi.json")
            response.raise_for_status()
            (ROOT / ".m4/openapi.json").write_text(
                json.dumps(response.json(), ensure_ascii=False, indent=2), "utf-8"
            )
        with httpx.Client(timeout=10, trust_env=False) as client:
            web = client.get("http://127.0.0.1:5173").status_code
        tests = ET.parse(ROOT / ".m4/backend-results.xml").getroot().find("testsuite")
        browser = json.loads((ROOT / ".m4/browser-results.json").read_text("utf-8"))["stats"]
        previous = Decimal(
            json.loads((ROOT / ".m3/final-summary.json").read_text("utf-8"))[
                "application_estimated_total_cny"
            ]
        )
        report = {
            "verified_at_utc": datetime.now(UTC).isoformat(),
            "health": health,
            "web_status": web,
            "migration": migration,
            "business_tables": tables,
            "active_runs": runs,
            "active_documents": documents,
            "rules": [dict(r) for r in rules],
            "backend_tests": dict(tests.attrib),
            "browser_tests": browser,
            "local_calculator_cases_passed": sum(r["passed"] for r in examples),
            "calculator_model_calls": 0,
            "application_estimated_total_cny": str(total),
            "m4_estimated_increment_cny": str(total - previous),
            "budget_limit_cny": str(settings.budget_limit_cny),
            "note": "Fictitious-policy M4 checks, not final fixed 40-case acceptance. Costs include unknown reservations and are not provider bills.",
        }
        assert migration == "0004_m4" and tables == 20 and runs == 0 and documents == 5
        assert health["phase"] == "M4" and web == 200
        assert len(rules) == 5 and all(r["status"] == "active" for r in rules)
        assert (
            int(tests.attrib["tests"]) >= 77
            and tests.attrib["failures"] == "0"
            and tests.attrib["errors"] == "0"
        )
        assert browser["expected"] == 11 and browser["unexpected"] == 0 and browser["skipped"] == 0
        (ROOT / ".m4/final-summary.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str), "utf-8"
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    examples = calculator_examples()
    factory = asyncio.SelectorEventLoop if os.name == "nt" else asyncio.new_event_loop
    with asyncio.Runner(loop_factory=factory) as runner:
        runner.run(verify(examples))
