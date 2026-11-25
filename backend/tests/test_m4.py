import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from sqlalchemy import select

from app.core.security import now
from app.models import Calculation, DocumentVersion, FeeRule
from tests.conftest import login
from tests.test_m2 import FakeEmbedder, process, upload
from tests.test_m3 import ScriptedModel, ScriptedProvider, execute, messages, submit

API = "/api/v1"
TRAVEL = "# 虚构演示住宿制度\n\n北京、上海：S1为300元/晚，S2为500元/晚。杭州、南京：S1为200元/晚，S2为350元/晚。未列明城市不自动归类。"
DAILY = "# 虚构演示补贴制度\n\n出差补贴统一80元/天。预算为住宿标准乘晚数加80元乘出差天数，完整合计须有全部有效规则。"


def payload(chunk, **extra):
    return {
        "name": "住宿规则",
        "kind": "lodging",
        "grade": "S1",
        "cities": ["北京", "上海"],
        "unit_rate": "300.00",
        "source_chunk_id": chunk["chunk_id"],
        "source_quote": chunk["content"],
        **extra,
    }


def create_rule(client, admin, body, active=True):
    response = client.post(API + "/admin/fee-rules", headers=admin, json=body)
    assert response.status_code == 201, response.text
    rule = response.json()["data"]
    if active:
        response = client.post(
            API + f"/admin/fee-rules/{rule['id']}/activate",
            headers=admin,
            json={"expected_revision": rule["revision"], "source_checked": True},
        )
        assert response.status_code == 200, response.text
        rule = response.json()["data"]
    return rule


@pytest.fixture
def fee_setup(client, records):
    client.app.state.embedder = FakeEmbedder()
    client.app.state.chat_provider = ScriptedProvider()
    admin = login(client)
    docs = [upload(client, records, admin, content=text.encode()) for text in [TRAVEL, DAILY]]
    for doc in docs:
        process(client, doc)
    chunks = [
        client.get(
            API + f"/admin/document-versions/{doc['version_id']}/chunks", headers=admin
        ).json()["data"]["items"][0]
        for doc in docs
    ]
    rules = [
        create_rule(
            client,
            admin,
            payload(
                chunks[0], name=f"{grade}-{cities[0]}", grade=grade, cities=cities, unit_rate=rate
            ),
        )
        for grade, cities, rate in [
            ("S1", ["北京", "上海"], "300.00"),
            ("S2", ["北京", "上海"], "500.00"),
            ("S1", ["杭州", "南京"], "200.00"),
            ("S2", ["杭州", "南京"], "350.00"),
        ]
    ]
    rules.append(
        create_rule(
            client,
            admin,
            payload(
                chunks[1], name="每日补贴", kind="daily", grade=None, cities=[], unit_rate="80.00"
            ),
        )
    )
    client.put(
        API + f"/admin/knowledge-bases/{records['common']}/members",
        headers=admin,
        json={"user_ids": [records["alice"], records["bob"]], "expected_revision": 1},
    )
    return client, records, admin, docs, chunks, rules


def conversation(client, records, username="alice"):
    actor = login(client, username)
    conv = client.post(
        API + "/conversations", headers=actor, json={"knowledge_base_ids": [records["common"]]}
    ).json()["data"]
    return actor, conv


def calculate(client, actor, conv, trip, **extra):
    response = client.post(
        API + f"/conversations/{conv['id']}/calculations",
        headers=actor,
        json={
            "client_request_id": str(uuid.uuid4()),
            "context_revision": conv["context_revision"],
            "profile_revision": 1,
            "trip_context": trip,
            **extra,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


@pytest.mark.parametrize(
    "username,city,days,nights,total,lodging,daily",
    [
        ("alice", "北京", 3, 2, "840.00", "600.00", "240.00"),
        ("bob", "北京", 3, 2, "1240.00", "1000.00", "240.00"),
        ("alice", "杭州", 3, 2, "640.00", "400.00", "240.00"),
        ("bob", "南京", 2, 1, "510.00", "350.00", "160.00"),
        ("alice", "上海", 1, 0, "80.00", "0.00", "80.00"),
        ("alice", "北京", 30, 29, "11100.00", "8700.00", "2400.00"),
    ],
)
def test_fixed_budget_values(fee_setup, username, city, days, nights, total, lodging, daily):
    client, records, _, _, _, _ = fee_setup
    actor, conv = conversation(client, records, username)
    result = calculate(
        client, actor, conv, {"destination": city, "travel_days": days, "lodging_nights": nights}
    )["calculation"]
    assert result["status"] == "complete" and result["total_amount"] == total
    assert {i["kind"]: i["amount"] for i in result["items"]} == {"lodging": lodging, "daily": daily}
    assert client.get(API + f"/calculations/{result['id']}", headers=actor).status_code == 200
    values = messages(client, actor, conv)
    assert [m["role"] for m in values] == ["user", "assistant"]
    assert len(values[-1]["citations"]) == 2


@pytest.mark.parametrize(
    "trip",
    [
        {"destination": "北京", "travel_days": 3, "lodging_nights": -1},
        {"destination": "北京", "travel_days": True, "lodging_nights": 0},
        {"destination": "北京", "travel_days": 3, "lodging_nights": 3},
        {"destination": "北京", "travel_days": 31, "lodging_nights": 2},
        {"destination": "北京", "travel_days": 3, "lodging_nights": 2, "grade": "S2"},
    ],
)
def test_invalid_conditions_do_not_create_amounts(fee_setup, trip):
    client, records, _, _, _, _ = fee_setup
    actor, conv = conversation(client, records)
    response = client.post(
        API + f"/conversations/{conv['id']}/calculations",
        headers=actor,
        json={
            "client_request_id": str(uuid.uuid4()),
            "context_revision": 1,
            "profile_revision": 1,
            "trip_context": trip,
        },
    )
    assert response.status_code == 422
    assert messages(client, actor, conv) == []


@pytest.mark.parametrize(
    "trip,reason",
    [
        ({"destination": "广州", "travel_days": 3, "lodging_nights": 2}, "blocked"),
        ({"destination": "北京市", "travel_days": 3, "lodging_nights": 2}, "blocked"),
        ({"destination": "北京", "travel_days": 3}, "needs_information"),
    ],
)
def test_unknown_city_or_missing_nights_has_no_complete_total(fee_setup, trip, reason):
    client, records, _, _, _, _ = fee_setup
    actor, conv = conversation(client, records)
    result = calculate(client, actor, conv, trip)["calculation"]
    assert result["status"] == reason and result["total_amount"] is None
    assert result["items"][0]["kind"] == "daily" and result["items"][0]["amount"] == "240.00"


def test_rule_activation_conflict_revision_and_source_validation(fee_setup):
    client, _, admin, _, chunks, rules = fee_setup
    bad = client.post(
        API + "/admin/fee-rules", headers=admin, json=payload(chunks[0], source_quote="编造的原文")
    )
    assert bad.status_code == 409
    rule = create_rule(client, admin, payload(chunks[0]), active=False)
    endpoint = API + f"/admin/fee-rules/{rule['id']}/activate"
    assert client.post(endpoint, headers=admin, json={"expected_revision": 1}).status_code == 422
    assert (
        client.post(
            endpoint, headers=admin, json={"expected_revision": 1, "source_checked": True}
        ).status_code
        == 409
    )
    old = rules[0]
    changed = client.put(
        API + f"/admin/fee-rules/{old['id']}",
        headers=admin,
        json={**payload(chunks[0], unit_rate="300.01"), "expected_revision": old["revision"]},
    )
    assert changed.status_code == 200 and changed.json()["data"]["status"] == "draft"
    assert (
        client.post(
            API + f"/admin/fee-rules/{old['id']}/suspend",
            headers=admin,
            json={"expected_revision": old["revision"]},
        ).status_code
        == 409
    )
    assert (
        client.post(
            API + "/admin/fee-rules", headers=admin, json=payload(chunks[0], unit_rate="0.001")
        ).status_code
        == 422
    )


def test_decimal_arithmetic_and_rule_change_hides_old_result(fee_setup):
    client, records, admin, _, chunks, rules = fee_setup
    actor, conv = conversation(client, records)
    first = calculate(
        client, actor, conv, {"destination": "北京", "travel_days": 3, "lodging_nights": 2}
    )
    rule = rules[0]
    edit = client.put(
        API + f"/admin/fee-rules/{rule['id']}",
        headers=admin,
        json={**payload(chunks[0], unit_rate="300.01"), "expected_revision": rule["revision"]},
    ).json()["data"]
    assert (
        client.post(
            API + f"/admin/fee-rules/{rule['id']}/activate",
            headers=admin,
            json={"expected_revision": edit["revision"], "source_checked": True},
        ).status_code
        == 200
    )
    assert (
        client.get(API + f"/calculations/{first['calculation']['id']}", headers=actor).status_code
        == 404
    )
    assert messages(client, actor, conv)[-1]["content"] is None
    second = calculate(
        client,
        actor,
        first["conversation"],
        {"destination": "北京", "travel_days": 3, "lodging_nights": 2},
    )
    assert second["calculation"]["total_amount"] == "840.02"


def test_source_replacement_suspends_only_related_rules_and_blocks_total(fee_setup):
    client, records, admin, docs, _, rules = fee_setup
    actor, conv = conversation(client, records)
    first = calculate(
        client, actor, conv, {"destination": "北京", "travel_days": 3, "lodging_nights": 2}
    )
    replaced = client.post(
        API + f"/admin/documents/{docs[0]['document_id']}/versions",
        headers=admin,
        data={"expected_current_version_id": docs[0]["version_id"]},
        files={"file": ("new.md", TRAVEL.replace("300", "320").encode())},
    ).json()["data"]
    process(client, replaced)
    current = client.get(API + "/admin/fee-rules", headers=admin).json()["data"]["items"]
    assert all(r["status"] == ("active" if r["kind"] == "daily" else "suspended") for r in current)
    assert (
        client.get(API + f"/calculations/{first['calculation']['id']}", headers=actor).status_code
        == 404
    )
    assert messages(client, actor, conv)[-1]["status"] == "source_unavailable"
    blocked = calculate(
        client,
        actor,
        first["conversation"],
        {"destination": "北京", "travel_days": 3, "lodging_nights": 2},
    )["calculation"]
    assert blocked["total_amount"] is None and len(blocked["items"]) == 1


def test_expired_source_is_never_used_even_if_rule_status_active(fee_setup):
    client, records, _, docs, _, _ = fee_setup
    actor, conv = conversation(client, records)

    async def expire():
        async with client.app.state.sessions() as db:
            row = await db.get(DocumentVersion, uuid.UUID(docs[0]["version_id"]))
            row.effective_until = now() - timedelta(seconds=1)
            await db.commit()

    client.portal.call(expire)
    result = calculate(
        client, actor, conv, {"destination": "北京", "travel_days": 3, "lodging_nights": 2}
    )["calculation"]
    assert result["status"] == "blocked" and result["total_amount"] is None


def test_owner_scope_idempotence_and_revisions(fee_setup):
    client, records, admin, _, chunks, _ = fee_setup
    actor, conv = conversation(client, records)
    assert client.get(API + "/admin/fee-rules", headers=actor).status_code == 403
    assert (
        client.post(API + "/admin/fee-rules", headers=actor, json=payload(chunks[0])).status_code
        == 403
    )
    key = str(uuid.uuid4())
    body = {
        "client_request_id": key,
        "context_revision": 1,
        "profile_revision": 1,
        "trip_context": {"destination": "北京", "travel_days": 3, "lodging_nights": 2},
    }
    endpoint = API + f"/conversations/{conv['id']}/calculations"
    response = client.post(endpoint, headers=actor, json=body)
    calc = response.json()["data"]["calculation"]
    assert (
        client.post(endpoint, headers=actor, json=body).json()["data"]["calculation"]["id"]
        == calc["id"]
    )
    assert (
        client.post(endpoint, headers=actor, json={**body, "profile_revision": 2}).status_code
        == 409
    )
    assert client.get(API + f"/calculations/{calc['id']}", headers=admin).status_code == 404
    assert (
        client.post(
            endpoint, headers=actor, json={**body, "client_request_id": str(uuid.uuid4())}
        ).status_code
        == 409
    )
    client.put(
        API + f"/admin/knowledge-bases/{records['common']}/members",
        headers=admin,
        json={"user_ids": [records["bob"]], "expected_revision": 2},
    )
    assert client.get(API + f"/calculations/{calc['id']}", headers=actor).status_code == 404
    assert "calculation" not in messages(client, actor, conv)[-1]


@pytest.mark.parametrize("confirmed", [False, True])
def test_agent_proposal_confirmation_and_canonical_program_amounts(
    fee_setup, monkeypatch, confirmed
):
    client, records, _, _, _, _ = fee_setup
    actor, conv = conversation(client, records)
    if confirmed:
        conv = client.patch(
            API + f"/conversations/{conv['id']}/trip-context",
            headers=actor,
            json={
                "expected_revision": 1,
                "trip_context": {"destination": "北京", "travel_days": 3, "lodging_nights": 2},
            },
        ).json()["data"]

    async def scripted(self, messages, **kwargs):
        self.calls += 1
        if self.calls == 1:
            name = "calculate_travel_budget" if confirmed else "propose_trip_context"
            args = (
                {} if confirmed else {"destination": "北京", "travel_days": 3, "lodging_nights": 2}
            )
        else:
            name, args = (
                "Answer",
                {
                    "status": "answered",
                    "basis": "calculation" if confirmed else "trip_proposal",
                    "answer": "模型试图输出错误总额999999元",
                    "citations": [],
                    "missing_fields": [],
                },
            )
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content="",
                        tool_calls=[{"name": name, "args": args, "id": uuid.uuid4().hex}],
                        usage_metadata={
                            "input_tokens": 100,
                            "output_tokens": 50,
                            "total_tokens": 150,
                        },
                    )
                )
            ]
        )

    monkeypatch.setattr(ScriptedModel, "_agenerate", scripted)
    extra = (
        {"budget_confirmed_revision": conv["context_revision"], "budget_profile_revision": 1}
        if confirmed
        else {}
    )
    run, _ = submit(client, actor, conv, content="北京出差3天2晚，预算多少？", **extra)
    execute(client, run)
    last = messages(client, actor, conv)[-1]
    assert "999999" not in last["content"]
    saved = client.get(API + f"/conversations/{conv['id']}", headers=actor).json()["data"]
    if confirmed:
        assert last["calculation"]["total_amount"] == "840.00" and "840.00" in last["content"]
    else:
        assert saved["trip_context"] == {} and "calculation" not in last
        assert last["trip_proposal"]["trip_context"]["lodging_nights"] == 2


def test_stale_employee_profile_blocks_both_confirmation_paths(fee_setup):
    client, records, admin, _, _, _ = fee_setup
    actor, conv = conversation(client, records)
    changed = client.patch(
        API + f"/admin/employees/{records['alice']}",
        headers=admin,
        json={
            "display_name": "Alice",
            "department": "测试部",
            "grade": "S2",
            "expected_revision": 1,
        },
    )
    assert changed.status_code == 200
    response = client.post(
        API + f"/conversations/{conv['id']}/calculations",
        headers=actor,
        json={
            "client_request_id": str(uuid.uuid4()),
            "context_revision": 1,
            "profile_revision": 1,
            "trip_context": {"destination": "北京", "travel_days": 3, "lodging_nights": 2},
        },
    )
    assert response.status_code == 409
    response = client.post(
        API + f"/conversations/{conv['id']}/messages",
        headers=actor,
        json={
            "client_request_id": str(uuid.uuid4()),
            "content": "确认预算",
            "context_revision": 1,
            "budget_confirmed_revision": 1,
            "budget_profile_revision": 1,
        },
    )
    assert response.status_code == 409
    assert messages(client, actor, conv) == []


def test_runtime_conflicting_rules_do_not_choose_an_arbitrary_rate(fee_setup):
    client, records, _, _, chunks, _ = fee_setup
    actor, conv = conversation(client, records)

    async def conflict():
        async with client.app.state.sessions() as db:
            existing = await db.scalar(
                select(FeeRule).where(FeeRule.kind == "lodging", FeeRule.grade == "S1")
            )
            db.add(
                FeeRule(
                    name="冲突变体",
                    kind="lodging",
                    grade="S1",
                    cities=["北京"],
                    unit_rate=Decimal("320.00"),
                    source_chunk_id=uuid.UUID(chunks[0]["chunk_id"]),
                    source_quote=chunks[0]["content"],
                    status="active",
                    confirmed_by=existing.confirmed_by,
                    confirmed_at=now(),
                )
            )
            await db.commit()

    client.portal.call(conflict)
    result = calculate(
        client, actor, conv, {"destination": "北京", "travel_days": 3, "lodging_nights": 2}
    )["calculation"]
    assert result["total_amount"] is None
    assert result["blocked_reasons"][0]["code"] == "RULE_CONFLICT"
    assert [i["kind"] for i in result["items"]] == ["daily"]


def test_deleted_source_hides_budget_and_preserves_unaffected_daily_rule(fee_setup):
    client, records, admin, docs, _, _ = fee_setup
    actor, conv = conversation(client, records)
    first = calculate(
        client, actor, conv, {"destination": "北京", "travel_days": 3, "lodging_nights": 2}
    )
    assert (
        client.delete(API + f"/admin/documents/{docs[0]['document_id']}", headers=admin).status_code
        == 204
    )
    assert (
        client.get(API + f"/calculations/{first['calculation']['id']}", headers=actor).status_code
        == 404
    )
    result = calculate(
        client,
        actor,
        first["conversation"],
        {"destination": "北京", "travel_days": 3, "lodging_nights": 2},
    )["calculation"]
    assert result["status"] == "blocked" and result["items"][0]["amount"] == "240.00"
    assert messages(client, actor, conv)[1]["content"] is None


def test_agent_calculator_without_explicit_confirmation_only_proposes(fee_setup, monkeypatch):
    client, records, _, _, _, _ = fee_setup
    actor, conv = conversation(client, records)
    conv = client.patch(
        API + f"/conversations/{conv['id']}/trip-context",
        headers=actor,
        json={
            "expected_revision": 1,
            "trip_context": {"destination": "北京", "travel_days": 3, "lodging_nights": 2},
        },
    ).json()["data"]

    async def scripted(self, messages, **kwargs):
        self.calls += 1
        name, args = (
            ("calculate_travel_budget", {})
            if self.calls == 1
            else (
                "Answer",
                {
                    "status": "answered",
                    "basis": "calculation",
                    "answer": "错误金额999999",
                    "citations": [],
                    "missing_fields": [],
                },
            )
        )
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content="",
                        tool_calls=[{"name": name, "args": args, "id": uuid.uuid4().hex}],
                        usage_metadata={
                            "input_tokens": 100,
                            "output_tokens": 50,
                            "total_tokens": 150,
                        },
                    )
                )
            ]
        )

    monkeypatch.setattr(ScriptedModel, "_agenerate", scripted)
    run, _ = submit(client, actor, conv, content="预算多少？")
    execute(client, run)
    last = messages(client, actor, conv)[-1]
    assert (
        last.get("trip_proposal") and "calculation" not in last and "999999" not in last["content"]
    )

    async def no_snapshot():
        async with client.app.state.sessions() as db:
            assert await db.scalar(select(Calculation.id)) is None

    client.portal.call(no_snapshot)
