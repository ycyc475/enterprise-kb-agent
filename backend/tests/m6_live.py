"""Explicit-only real Qwen evaluation: run via scripts/m6_evaluate.py --live."""

import asyncio
import hashlib
import json
import os
import time
import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from langchain_core.callbacks import BaseCallbackHandler
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hasher
from app.main import create_app
from app.models import EmployeeProfile, KBGrant, KnowledgeBase, UsageRecord, User
from app.services.chat_agent import SYSTEM
from tests.conftest import ORIGIN, PASSWORD, login
from tests.m6_support import API, ROOT, manifest, prepare, save_json
from tests.test_m2 import process, upload
from tests.test_m3 import execute, messages, submit
from tests.test_m4 import calculate

CASES = json.loads((ROOT / "data/evaluation/m6-cases.json").read_text("utf-8"))["cases"]


class ToolTrace(BaseCallbackHandler):
    def __init__(self):
        self.rows = []

    def on_llm_end(self, response, **kwargs):
        for group in response.generations:
            for generation in group:
                message = generation.message
                self.rows.append({"content": message.content, "tool_calls": message.tool_calls})


@pytest.fixture(scope="module")
def live(test_settings):
    if os.environ.get("M6_LIVE_AUTHORIZED") != "1":
        pytest.fail("Use scripts/m6_evaluate.py --live to reserve the evaluation budget first")
    original = get_settings()
    assert original.dashscope_api_key.get_secret_value()
    settings = test_settings.model_copy(
        update={
            "dashscope_api_key": original.dashscope_api_key,
            "dashscope_base_url": original.dashscope_base_url,
            "chat_model": original.chat_model,
            "budget_warning_cny": Decimal("0.80"),
            "budget_limit_cny": Decimal("1.00"),
            "storage_dir": ROOT / ".m6/isolated-files" / test_settings.postgres_db,
        }
    )
    engine = create_engine(settings.database_url)
    records = {}
    with Session(engine) as db:
        for username, role, grade in [
            ("admin", "admin", "S2"),
            ("alice", "employee", "S1"),
            ("bob", "employee", "S2"),
        ]:
            user = User(
                username=username,
                role=role,
                password_hash=hasher.hash(PASSWORD),
                must_change_password=False,
            )
            db.add(user)
            db.flush()
            db.add(
                EmployeeProfile(
                    user_id=user.id,
                    display_name=username,
                    department="研发部" if username == "bob" else "产品部",
                    grade=grade,
                )
            )
            records[username] = str(user.id)
        for name in ["common", "restricted"]:
            kb = KnowledgeBase(name=name, description="M6独立验收资料")
            db.add(kb)
            db.flush()
            records[name] = str(kb.id)
        db.add(KBGrant(user_id=uuid.UUID(records["alice"]), kb_id=uuid.UUID(records["common"])))
        db.add(KBGrant(user_id=uuid.UUID(records["bob"]), kb_id=uuid.UUID(records["restricted"])))
        db.commit()
    options = {"loop_factory": asyncio.SelectorEventLoop} if os.name == "nt" else {}
    report = {
        "evaluation_id": os.environ["M6_EVALUATION_ID"],
        "system_prompt_sha256": hashlib.sha256(SYSTEM.encode()).hexdigest(),
        "cases_sha256": hashlib.sha256(
            (ROOT / "data/evaluation/m6-cases.json").read_bytes()
        ).hexdigest(),
        "materials": manifest(),
        "model": settings.chat_model,
        "embedding": "text-embedding-v4/1024/v1",
        "limits": [5, 10, 90],
        "results": [],
        "review_status": "pending_independent_source_review",
        "database_isolated": True,
    }
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
            client.app.state.m6_trace = trace
            try:
                admin = login(client)
                docs, chunks, rules = prepare(client, records, admin)
                report["fixture_documents"] = docs
                yield client, records, admin, docs, report
            finally:
                with Session(engine) as db:
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
    finally:
        engine.dispose()


def turn(client, actor, conv, question, **extra):
    start = time.monotonic()
    run, _ = submit(client, actor, conv, content=question, **extra)
    execute(client, run)
    state = client.get(API + f"/runs/{run['id']}", headers=actor).json()["data"]
    trace_resp = client.get(API + f"/runs/{run['id']}/trace", headers=actor)
    run_trace = trace_resp.json()["data"] if trace_resp.status_code == 200 else None
    answer = messages(client, actor, conv)[-1]
    sources = []
    for cite in answer["citations"]:
        source = client.get(API + f"/citations/{cite['id']}", headers=actor)
        assert source.status_code == 200
        sources.append(source.json()["data"])
    return {
        "question": question,
        "answer": answer,
        "run": state,
        "trace": run_trace,
        "sources": sources,
        "elapsed_seconds": round(time.monotonic() - start, 3),
    }


def confirm(client, actor, conv, row, question="条件已核对，请计算标准预算。"):
    proposal = row["answer"].get("trip_proposal")
    assert proposal, row["run"]
    updated = client.patch(
        API + f"/conversations/{conv['id']}/trip-context",
        headers=actor,
        json={
            "expected_revision": proposal["context_revision"],
            "trip_context": proposal["trip_context"],
        },
    )
    assert updated.status_code == 200
    conv = updated.json()["data"]
    return conv, turn(
        client,
        actor,
        conv,
        question,
        budget_confirmed_revision=conv["context_revision"],
        budget_profile_revision=proposal["profile_revision"],
    )


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_live_question(live, case):
    client, records, admin, docs, report = live
    client.app.state.m6_trace.rows = []
    actor = login(client, case["user"])
    scopes = [records["common"]] + ([records["restricted"]] if case["user"] == "bob" else [])
    conv = client.post(
        API + "/conversations",
        headers=actor,
        json={"title": "M6-" + case["id"], "knowledge_base_ids": scopes},
    ).json()["data"]
    conflict = None
    rows = []
    try:
        if case["id"] == "QA13":
            initial = calculate(
                client, actor, conv, {"destination": "北京", "travel_days": 3, "lodging_nights": 2}
            )
            assert initial["calculation"]["total_amount"] == "840.00"
            conv = initial["conversation"]
        if case["id"] == "QA19":
            conflict = upload(
                client,
                records,
                admin,
                name="POL-CONFLICT.md",
                content=(ROOT / "data/evaluation/m6-conflict-variant.md").read_bytes(),
                title="POL-CONFLICT 虚构演示冲突制度",
            )
            process(client, conflict)
        rows.append(turn(client, actor, conv, case["question"]))
        if case["id"] in {"QA12", "QA13", "QA17"}:
            prompt = (
                "条件已核对，请计算标准预算，并整理从申请到报销的办理指南。"
                if case["id"] == "QA12"
                else "条件已核对，请计算标准预算。"
            )
            conv, row = confirm(client, actor, conv, rows[-1], prompt)
            rows.append(row)
        final = rows[-1]["answer"]
        sources = {
            code
            for code in case["sources"]
            if any(("POL-" + code) in s["title"] for s in rows[-1]["sources"])
        }
        basic = all(
            word in (final.get("content") or "") for word in case["contains"]
        ) and sources == set(case["sources"])
        if case["id"] == "QA10":
            proposal = final.get("trip_proposal") or {}
            basic = (
                proposal.get("trip_context") == {"destination": "北京", "travel_days": 3}
                and "lodging_nights" in final["missing_fields"]
                and not final.get("calculation")
            )
        if case["id"] == "QA13":
            basic = basic and rows[0]["answer"]["trip_proposal"]["trip_context"] == {
                "destination": "杭州",
                "travel_days": 3,
                "lodging_nights": 2,
            }
        if case["id"] == "QA17":
            basic = (final.get("calculation") or {}).get("total_amount") is None and final[
                "status"
            ] == "insufficient_evidence"
        if case["id"] in {"QA18", "QA20"}:
            basic = final["status"] == "insufficient_evidence" and not final["citations"]
        report["results"].append(
            {
                **case,
                "turns": rows,
                "basic_checks_passed": basic,
                "source_review": "pending",
                "tool_trace": client.app.state.m6_trace.rows,
            }
        )
        save_json(ROOT / ".m6/qa-results.json", report)
        # These checks are diagnostic. Passing them never substitutes for source-by-source review.
        assert all(
            row["run"]["status"] in {"completed", "needs_clarification", "insufficient_evidence"}
            for row in rows
        ), [(r["run"]["status"], r["run"]["stop_reason"]) for r in rows]
    finally:
        client.delete(API + f"/conversations/{conv['id']}", headers=actor)
        if conflict:
            assert (
                client.delete(
                    API + f"/admin/documents/{conflict['document_id']}", headers=admin
                ).status_code
                == 204
            )


@pytest.mark.parametrize(
    "case,question,candidate,relevant",
    [
        ("GROUND-UNRELATED", "公司工资制度规定每月几号发薪？", "公司每月15号发薪。", False),
        ("GROUND-UNSUPPORTED", "S1去北京住宿标准是多少？", "S1去北京住宿标准为500元/晚。", True),
    ],
)
def test_live_grounding_guard(live, case, question, candidate, relevant):
    """A component check with deliberately wrong drafts, outside the frozen 20 QA cases."""
    from langchain_core.messages import HumanMessage

    from app.chat_protocol import Answer
    from app.services.answer_grounding import review_answer
    from app.services.chat_agent import Execution, finish

    client, records, admin, docs, report = live
    actor = login(client, "alice")
    conv = client.post(
        API + "/conversations",
        headers=actor,
        json={"title": case, "knowledge_base_ids": [records["common"]]},
    ).json()["data"]
    sources = client.get(
        API + f"/admin/document-versions/{docs['TRAVEL']['version_id']}/chunks", headers=admin
    ).json()["data"]["items"]
    source = next(s for s in sources if "甲类" in s["content"] and "300元/晚" in s["content"])
    run, _ = submit(client, actor, conv, content=question)

    async def check():
        execution = Execution(client.app, uuid.UUID(run["id"]))
        execution.question_text = execution.knowledge_question = question
        execution.seen[source["chunk_id"]] = source
        await execution.guard()
        draft = Answer(
            status="answered",
            basis="documents",
            answer=candidate,
            citations=[{"chunk_id": source["chunk_id"], "quote": source["content"]}],
            missing_fields=[],
        )
        verdict = await review_answer(execution, draft, [HumanMessage(content=question)])
        execution.evidence_review = verdict.model_dump()
        await finish(
            execution,
            Answer(
                status="insufficient_evidence",
                basis="unavailable",
                answer="证据核验未通过。",
                citations=[],
                missing_fields=[],
            ),
        )
        return verdict

    try:
        verdict = client.portal.call(check)
        last = messages(client, actor, conv)[-1]
        report["results"].append(
            {
                "id": case,
                "kind": "grounding_component_check",
                "question": question,
                "deliberately_wrong_candidate": candidate,
                "quote": source["content"],
                "verdict": verdict.model_dump(),
                "answer": last,
                "basic_checks_passed": verdict.relevant == relevant and not verdict.supported,
            }
        )
        assert verdict.relevant == relevant and not verdict.supported
        assert last["status"] == "insufficient_evidence" and not last["citations"]
        assert candidate not in last["content"]
    finally:
        client.delete(API + f"/conversations/{conv['id']}", headers=actor)
