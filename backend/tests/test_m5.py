import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core.errors import ApiError
from app.core.security import now
from app.models import DocumentVersion, UsageRecord
from app.services import audit, fees
from app.services.chat_models import reserve
from tests.conftest import PASSWORD
from tests.test_m2 import process
from tests.test_m3 import execute, messages, submit
from tests.test_m4 import TRAVEL, calculate, conversation, create_rule, payload
from tests.test_m4 import fee_setup as fee_setup

API = "/api/v1"


def events(client, admin, **query):
    response = client.get(API + "/admin/audit-events", headers=admin, params=query)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def replace(client, admin, doc, content):
    response = client.post(
        API + f"/admin/documents/{doc['document_id']}/versions",
        headers=admin,
        data={"expected_current_version_id": doc["version_id"]},
        files={"file": ("updated.md", content.encode())},
    )
    assert response.status_code == 202, response.text
    item = response.json()["data"]
    process(client, item)
    return item


def review(client, admin, rule):
    response = client.get(API + f"/admin/fee-rules/{rule['id']}/source-review", headers=admin)
    assert response.status_code == 200
    return response.json()["data"]


def recovery_body(value):
    return {
        "expected_revision": value["rule"]["revision"],
        "expected_source_version_id": value["current_version_id"],
        "source_chunk_id": value["candidates"][0]["chunk_id"],
        "source_quote": value["candidates"][0]["content"],
        "unit_rate": "320.00",
        "source_checked": True,
    }


def test_replacement_review_recovery_and_history_840_to_880(fee_setup):
    client, records, admin, docs, _, rules = fee_setup
    actor, conv = conversation(client, records)
    old = calculate(
        client, actor, conv, {"destination": "北京", "travel_days": 3, "lodging_nights": 2}
    )
    replace(client, admin, docs[0], TRAVEL.replace("300", "320"))
    value = review(client, admin, rules[0])
    assert "300" in value["previous_source"]["quote"] and "320" in value["candidates"][0]["content"]
    assert value["rule"]["status"] == "suspended"
    blocked = calculate(
        client,
        actor,
        old["conversation"],
        {"destination": "北京", "travel_days": 3, "lodging_nights": 2},
    )
    assert blocked["calculation"]["total_amount"] is None
    response = client.post(
        API + f"/admin/fee-rules/{rules[0]['id']}/recover", headers=admin, json=recovery_body(value)
    )
    assert response.status_code == 200, response.text
    updated = calculate(
        client,
        actor,
        blocked["conversation"],
        {"destination": "北京", "travel_days": 3, "lodging_nights": 2},
    )
    assert updated["calculation"]["total_amount"] == "880.00"
    assert (
        client.get(API + f"/calculations/{old['calculation']['id']}", headers=actor).status_code
        == 404
    )
    assert messages(client, actor, conv)[1]["content"] is None
    history = events(client, admin, entity_type="rule", entity_id=rules[0]["id"])["items"]
    assert [e["action"] for e in history[:2]] == ["rule.recovered", "rule.source_invalidated"]
    assert (
        history[0]["before"]["unit_rate"] == "300.00"
        and history[0]["after"]["unit_rate"] == "320.00"
    )
    remaining = client.get(API + "/admin/fee-rules", headers=admin).json()["data"]["items"]
    assert (
        sum(r["status"] == "active" for r in remaining) == 2
    )  # Other lodging rules still require their own review.


@pytest.mark.parametrize("bad", ["old_source", "version", "other_document", "quote", "revision"])
def test_recovery_rejects_stale_or_unrelated_inputs_without_history(fee_setup, bad):
    client, _, admin, docs, chunks, rules = fee_setup
    replace(client, admin, docs[0], TRAVEL.replace("300", "320"))
    value = review(client, admin, rules[0])
    body = recovery_body(value)
    if bad == "old_source":
        body["source_chunk_id"] = chunks[0]["chunk_id"]
    if bad == "version":
        body["expected_source_version_id"] = str(uuid.uuid4())
    if bad == "other_document":
        body.update(
            source_chunk_id=chunks[1]["chunk_id"],
            expected_source_version_id=chunks[1]["version_id"],
            source_quote=chunks[1]["content"],
        )
    if bad == "quote":
        body["source_quote"] = "虚构摘录，不在实际原文"
    if bad == "revision":
        body["expected_revision"] = 1
    response = client.post(
        API + f"/admin/fee-rules/{rules[0]['id']}/recover", headers=admin, json=body
    )
    assert response.status_code == 409, response.text
    assert not events(client, admin, action="rule.recovered")["items"]
    assert review(client, admin, rules[0])["rule"]["unit_rate"] == "300.00"


def test_failed_replacement_keeps_old_rules_and_retry_logs_the_lifecycle(fee_setup):
    client, records, admin, docs, _, rules = fee_setup
    broken = replace(client, admin, docs[0], " ")
    assert review(client, admin, rules[0])["rule"]["status"] == "active"
    assert events(client, admin, action="document.processing_failed")["items"]
    assert not events(client, admin, action="rule.source_invalidated")["items"]
    response = client.post(API + f"/admin/ingestion-jobs/{broken['job_id']}/retry", headers=admin)
    assert response.status_code == 202
    assert events(client, admin, action="document.retry_requested")["items"]


def test_expiry_sweep_pauses_once_and_preserves_the_daily_rule(fee_setup):
    client, _, admin, docs, _, rules = fee_setup

    async def sweep():
        async with client.app.state.sessions() as db:
            version = await db.get(DocumentVersion, uuid.UUID(docs[0]["version_id"]))
            version.effective_from = now() - timedelta(days=2)
            version.effective_until = now() - timedelta(days=1)
            await db.commit()
        for _ in range(2):
            async with client.app.state.sessions() as db:
                await fees.reconcile_sources(db, client.app.state.settings)
                await db.commit()

    client.portal.call(sweep)
    history = events(client, admin, action="rule.source_invalidated")["items"]
    assert len(history) == 4 and all(
        e["after"]["suspension_reason"] == "SOURCE_EXPIRED" for e in history
    )
    current = client.get(API + "/admin/fee-rules", headers=admin).json()["data"]["items"]
    assert next(r for r in current if r["kind"] == "daily")["status"] == "active"


def test_kb_disable_requires_manual_restore_but_individual_revocation_does_not_pause_rules(
    fee_setup,
):
    client, records, admin, _, _, _ = fee_setup
    membership = client.get(
        API + f"/admin/knowledge-bases/{records['common']}/members", headers=admin
    ).json()["data"]
    client.put(
        API + f"/admin/knowledge-bases/{records['common']}/members",
        headers=admin,
        json={"user_ids": [records["bob"]], "expected_revision": membership["revision"]},
    )
    assert not events(client, admin, action="rule.source_invalidated")["items"]
    change = events(client, admin, action="knowledge_base.members_updated")["items"][0]
    assert change["after"]["member_names"] == ["bob"]
    kb = client.get(API + f"/knowledge-bases/{records['common']}", headers=admin).json()["data"]
    body = {
        "name": kb["name"],
        "description": kb["description"],
        "is_active": False,
        "expected_revision": kb["revision"],
    }
    response = client.patch(API + f"/admin/knowledge-bases/{kb['id']}", headers=admin, json=body)
    assert response.status_code == 200, response.text
    body.update(is_active=True, expected_revision=response.json()["data"]["revision"])
    assert (
        client.patch(
            API + f"/admin/knowledge-bases/{kb['id']}", headers=admin, json=body
        ).status_code
        == 200
    )
    current = client.get(API + "/admin/fee-rules", headers=admin).json()["data"]["items"]
    assert all(
        r["status"] == "suspended" and r["suspension_reason"] == "KNOWLEDGE_BASE_DISABLED"
        for r in current
    )


def test_audit_is_admin_only_paginated_and_contains_no_credentials_or_chat(fee_setup):
    client, records, admin, _, _, rules = fee_setup
    actor, conv = conversation(client, records)
    submit(client, actor, conv, content="PRIVATE_CHAT_SENTINEL_45")
    secret = "Only_for_test_Secret_2026!"
    created = client.post(
        API + "/admin/users",
        headers=admin,
        json={
            "username": "new_demo_user",
            "password": secret,
            "role": "employee",
            "display_name": "新员工",
            "department": "QA",
            "grade": "S1",
        },
    )
    assert created.status_code == 201
    reset = client.post(
        API + f"/admin/users/{records['bob']}/password-reset", headers=admin
    ).json()["data"]["temporary_password"]
    history = events(client, admin)
    assert secret not in str(history) and reset not in str(history) and PASSWORD not in str(history)
    assert all(
        token not in str(history)
        for token in ["password_hash", "refresh_token", "access_token", "PRIVATE_CHAT_SENTINEL_45"]
    )
    assert client.get(API + "/admin/audit-events", headers=actor).status_code == 403
    assert (
        client.get(
            API + f"/admin/fee-rules/{rules[0]['id']}/source-review", headers=actor
        ).status_code
        == 403
    )
    first = events(client, admin, limit=3)
    second = events(client, admin, limit=3, before_id=first["next_before_id"])
    assert {e["id"] for e in first["items"]}.isdisjoint(e["id"] for e in second["items"])
    filtered = events(client, admin, entity_type="rule", entity_id=rules[0]["id"])
    assert all(e["entity_id"] == rules[0]["id"] for e in filtered["items"])


@pytest.mark.parametrize(
    "statement", ["UPDATE audit_events SET actor_label='changed'", "DELETE FROM audit_events"]
)
def test_database_rejects_history_mutation(fee_setup, db_engine, statement):
    with pytest.raises(DBAPIError):
        with db_engine.begin() as db:
            db.execute(text(statement))


def test_audit_failure_rolls_back_the_management_mutation(fee_setup, monkeypatch):
    client, _, admin, _, _, rules = fee_setup
    old = rules[0]

    def unavailable(*args, **kwargs):
        raise RuntimeError("simulated history storage failure")

    monkeypatch.setattr(audit, "record", unavailable)
    with pytest.raises(RuntimeError, match="history storage"):
        client.post(
            API + f"/admin/fee-rules/{old['id']}/suspend",
            headers=admin,
            json={"expected_revision": old["revision"]},
        )
    fresh = review(client, admin, old)["rule"]
    assert fresh["status"] == "active" and fresh["revision"] == old["revision"]


def test_concurrent_activation_only_commits_one_matching_rule(fee_setup):
    client, _, admin, _, chunks, rules = fee_setup
    old = rules[0]
    client.post(
        API + f"/admin/fee-rules/{old['id']}/suspend",
        headers=admin,
        json={"expected_revision": old["revision"]},
    )
    drafts = [
        create_rule(client, admin, payload(chunks[0], name=f"竞争变体{i}"), active=False)
        for i in range(2)
    ]

    def activate(rule):
        return client.post(
            API + f"/admin/fee-rules/{rule['id']}/activate",
            headers=admin,
            json={"expected_revision": 1, "source_checked": True},
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(activate, drafts))
    assert sorted(statuses) == [200, 409]


def test_budget_warning_pause_unknown_reserve_and_model_free_calculation(fee_setup):
    client, records, admin, _, _, _ = fee_setup
    actor, conv = conversation(client, records)

    async def add_cost(amount, status):
        async with client.app.state.sessions() as db:
            db.add(
                UsageRecord(
                    model="test-model",
                    operation="chat",
                    status=status,
                    estimated_cost=Decimal(amount),
                )
            )
            await db.commit()

    client.portal.call(add_cost, "79.99", "estimated")
    client.portal.call(add_cost, "0.02", "failed_unknown")
    usage = client.get(API + "/admin/usage", headers=admin).json()["data"]
    assert (
        usage["warning"]
        and not usage["paused"]
        and Decimal(usage["unknown_reserved_cny"]) == Decimal("0.02")
    )
    client.portal.call(add_cost, "20", "estimated")
    assert client.get(API + "/admin/usage", headers=admin).json()["data"]["paused"]
    result = calculate(
        client, actor, conv, {"destination": "北京", "travel_days": 3, "lodging_nights": 2}
    )
    assert result["calculation"]["total_amount"] == "840.00"
    run, _ = submit(client, actor, result["conversation"])
    execute(client, run)
    assert (
        client.get(API + f"/runs/{run['id']}", headers=actor).json()["data"]["stop_reason"]
        == "BUDGET_PAUSED"
    )
    assert client.app.state.chat_provider.model.calls == 0


def test_simultaneous_budget_reservations_cannot_cross_limit(fee_setup):
    client, _, _, _, _, _ = fee_setup

    async def attempts():
        async with client.app.state.sessions() as db:
            db.add(
                UsageRecord(
                    model="test-model",
                    operation="chat",
                    status="estimated",
                    estimated_cost=Decimal("99.998"),
                )
            )
            await db.commit()
        results = await asyncio.gather(
            *(
                reserve(client.app.state.sessions, client.app.state.settings, "qwen3.7-flash", {})
                for _ in range(2)
            ),
            return_exceptions=True,
        )
        assert sum(isinstance(r, ApiError) and r.code == "BUDGET_PAUSED" for r in results) == 1
        assert sum(isinstance(r, uuid.UUID) for r in results) == 1
        async with client.app.state.sessions() as db:
            total = sum((await db.scalars(select(UsageRecord.estimated_cost))).all())
            assert total <= 100

    client.portal.call(attempts)
