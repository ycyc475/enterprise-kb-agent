"""Explicit paid smoke; only the newly generated fictional policy leaves the machine."""

import asyncio
import os
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.main import create_app
from app.models import UsageRecord
from tests.conftest import ORIGIN, login
from tests.m6_live import ToolTrace, turn
from tests.m6_support import API, ROOT, save_json
from tests.test_expenses import PARAMETERS, calculate, request
from tests.test_m2 import process, upload
from tests.test_m4 import create_rule


def test_real_citations_and_expense_confirmation(test_settings, records, db_engine):
    assert os.environ.get("M6_LIVE_AUTHORIZED") == "1", "Reserve budget via m6_evaluate.py"
    original = get_settings()
    settings = test_settings.model_copy(
        update={
            "dashscope_api_key": original.dashscope_api_key,
            "dashscope_base_url": original.dashscope_base_url,
            "chat_model": original.chat_model,
            "budget_warning_cny": Decimal("0.8"),
            "budget_limit_cny": Decimal("1"),
            "storage_dir": ROOT / ".m6/isolated-files" / test_settings.postgres_db,
        }
    )
    report = {"suite": "expense-reliability", "model": settings.chat_model, "results": []}
    options = {"loop_factory": asyncio.SelectorEventLoop} if os.name == "nt" else {}
    try:
        with TestClient(
            create_app(settings), backend_options=options, headers={"Origin": ORIGIN}
        ) as client:
            trace = ToolTrace()
            provider_create = client.app.state.chat_provider.create

            def traced_create(model):
                value = provider_create(model)
                value.callbacks = [trace]
                return value

            client.app.state.chat_provider.create = traced_create
            admin = login(client)
            doc = upload(
                client,
                records,
                admin,
                name="expense-policy.md",
                title="虚构演示综合费用制度",
                content=(
                    ROOT / "output/knowledge_documents/xingzhou_expense_policy_v1.md"
                ).read_bytes(),
            )
            process(client, doc)
            chunks = client.get(
                API + f"/admin/document-versions/{doc['version_id']}/chunks", headers=admin
            ).json()["data"]["items"]
            for kind, (rate, cap, ratio, maximum) in PARAMETERS.items():
                marker = {
                    "office": "OFFICE-01",
                    "training": "TRAINING-01",
                    "hospitality": "HOSPITALITY-01",
                }[kind]
                source = next(row for row in chunks if marker in row["content"])
                create_rule(
                    client,
                    admin,
                    {
                        "name": kind,
                        "kind": kind,
                        "grade": None,
                        "cities": [],
                        "unit_rate": rate,
                        "application_cap": cap,
                        "reimbursement_ratio": ratio,
                        "max_quantity": maximum,
                        "source_chunk_id": source["chunk_id"],
                        "source_quote": source["content"],
                    },
                )
            actor = login(client, "alice")
            cases = [
                (
                    "cite_travel",
                    "S1和S2在北京、上海、杭州、南京的住宿标准分别是多少？每日补贴是多少？请给出依据。",
                    None,
                ),
                (
                    "cite_training",
                    "培训费用的核算比例、人均上限、单次上限和人数限制分别是多少？请给出制度依据。",
                    None,
                ),
                (
                    "office_card",
                    "采购办公用品12套，实际申请金额2400元，请按制度核算。",
                    ("office", 12, "2400", "1500.00"),
                ),
                (
                    "training_card",
                    "培训3人，实际申请金额1234.56元，请按制度核算。",
                    ("training", 3, "1234.56", "987.65"),
                ),
                (
                    "hospitality_card",
                    "业务招待15人，实际申请金额3000元，请按制度核算。",
                    ("hospitality", 15, "3000", "2000.00"),
                ),
            ]
            failures = []
            for case_id, question, expected in cases:
                trace.rows = []
                conv = client.post(
                    API + "/conversations",
                    headers=actor,
                    json={"knowledge_base_ids": [records["common"]]},
                ).json()["data"]
                row = turn(client, actor, conv, question)
                row.update(case_id=case_id, tool_trace=trace.rows)
                report["results"].append(row)
                answer = row["answer"]
                if expected:
                    kind, quantity, amount, total = expected
                    proposal = answer.get("expense_proposal", {})
                    context = proposal.get("expense_context", {})
                    passed = (
                        context.get("kind") == kind
                        and context.get("quantity") == quantity
                        and Decimal(context.get("actual_amount", "-1")) == Decimal(amount)
                    )
                    if passed:
                        body = request(conv, kind, quantity, amount)
                        body["profile_revision"] = proposal["profile_revision"]
                        value = calculate(client, actor, conv, body).json()["data"]["calculation"]
                        row["calculation"] = value
                        passed = value["total_amount"] == total
                else:
                    passed = answer["status"] == "completed" and bool(row["sources"])
                    if case_id == "cite_travel":
                        source_text = "\n".join(source["quote"] for source in row["sources"])
                        passed = passed and all(
                            marker in source_text
                            for marker in [
                                "TRV-S1-A",
                                "TRV-S2-A",
                                "TRV-S1-B",
                                "TRV-S2-B",
                                "TRV-DAILY-01",
                            ]
                        )
                row["checks_passed"] = passed
                if not passed:
                    failures.append(case_id)
            assert not failures, "Failed live cases: " + ",".join(failures)
    finally:
        with Session(db_engine) as db:
            ledger = [
                {
                    field: getattr(row, field)
                    for field in [
                        "id",
                        "operation",
                        "model",
                        "status",
                        "input_tokens",
                        "output_tokens",
                        "estimated_cost",
                        "provider_request_id",
                        "price_snapshot",
                        "created_at",
                    ]
                }
                for row in db.scalars(select(UsageRecord)).all()
            ]
        save_json(
            ROOT / ".m6/live-usage.json",
            {"evaluation_id": os.environ["M6_EVALUATION_ID"], "records": ledger},
        )
        report["estimated_cost_cny"] = str(
            sum((row["estimated_cost"] for row in ledger), Decimal("0"))
        )
        save_json(ROOT / ".m6/qa-results.json", report)
