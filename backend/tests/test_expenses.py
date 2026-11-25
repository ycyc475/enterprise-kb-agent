import uuid

import pytest

from tests.conftest import login
from tests.test_m2 import process, upload
from tests.test_m3 import ScriptedModel, execute, messages, submit
from tests.test_m4 import conversation, create_rule
from tests.test_m4 import fee_setup as fee_setup
from tests.test_m6_regressions import response

API = "/api/v1"
PARAMETERS = {
    "office": ("150.00", "1500.00", "1", 20),
    "training": ("2000.00", "5000.00", "0.8", 10),
    "hospitality": ("150.00", "2000.00", "1", 30),
}


@pytest.fixture
def expenses(fee_setup):
    client, records, admin, *_ = fee_setup
    rules = {}
    for kind, (rate, cap, ratio, maximum) in PARAMETERS.items():
        doc = upload(
            client,
            records,
            admin,
            name=kind + ".md",
            title=kind + " 虚构费用制度",
            content=f"# 虚构{kind}\n\n单位上限{rate}元；单次上限{cap}元；比例{ratio}；最多{maximum}。".encode(),
        )
        process(client, doc)
        chunk = client.get(
            API + f"/admin/document-versions/{doc['version_id']}/chunks", headers=admin
        ).json()["data"]["items"][0]
        rules[kind] = create_rule(
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
                "source_chunk_id": chunk["chunk_id"],
                "source_quote": chunk["content"],
            },
        )
    return client, records, admin, rules


def request(conv, kind="office", quantity=5, amount="600.00", **extra):
    return {
        "client_request_id": str(uuid.uuid4()),
        "context_revision": conv["context_revision"],
        "profile_revision": 1,
        "expense_context": {"kind": kind, "quantity": quantity, "actual_amount": amount},
        **extra,
    }


def calculate(client, actor, conv, body):
    return client.post(
        API + f"/conversations/{conv['id']}/expense-calculations", headers=actor, json=body
    )


@pytest.mark.parametrize(
    "kind,quantity,amount,expected",
    [
        ("office", 5, "600.00", "600.00"),
        ("office", 12, "2400.00", "1500.00"),
        ("training", 2, "6000.00", "4000.00"),
        ("training", 3, "1234.56", "987.65"),
        ("training", 10, "100000.00", "5000.00"),
        ("hospitality", 15, "3000.00", "2000.00"),
        ("hospitality", 1, "100.00", "100.00"),
        ("office", 1, "0.00", "0.00"),
    ],
)
def test_independently_specified_amounts_and_source_citations(
    expenses, kind, quantity, amount, expected
):
    client, records, _, _ = expenses
    actor, conv = conversation(client, records)
    result = calculate(client, actor, conv, request(conv, kind, quantity, amount))
    assert result.status_code == 201, result.text
    value = result.json()["data"]["calculation"]
    assert value["total_amount"] == expected and value["status"] == "complete"
    assert value["expense_context"]["kind"] == kind and value["items"][0]["unit"] == (
        "套" if kind == "office" else "人"
    )
    last = messages(client, actor, conv)[-1]
    assert len(last["citations"]) == 1 and last["calculation"]["total_amount"] == expected
    source = client.get(API + f"/citations/{last['citations'][0]['id']}", headers=actor).json()[
        "data"
    ]
    assert kind in source["title"] and source["quote"]


def test_quantity_limit_never_becomes_a_zero_or_complete_total(expenses):
    client, records, _, _ = expenses
    actor, conv = conversation(client, records)
    value = calculate(client, actor, conv, request(conv, quantity=21)).json()["data"]["calculation"]
    assert value["total_amount"] is None and value["status"] == "blocked"
    assert value["blocked_reasons"][0]["code"] == "QUANTITY_LIMIT"


@pytest.mark.parametrize(
    "override",
    [
        {"quantity": True},
        {"quantity": 0},
        {"quantity": 101},
        {"actual_amount": "-1"},
        {"actual_amount": "NaN"},
        {"actual_amount": "1.001"},
        {"unit_rate": "0.01"},
        {"reimbursement_ratio": "1"},
        {"grade": "S2"},
        {"kind": "salary"},
    ],
)
def test_invalid_or_policy_injection_inputs_are_rejected(expenses, override):
    client, records, _, _ = expenses
    actor, conv = conversation(client, records)
    body = request(conv)
    body["expense_context"].update(override)
    assert calculate(client, actor, conv, body).status_code == 422
    assert messages(client, actor, conv) == []


def test_expense_idempotency_and_stale_confirmation(expenses):
    client, records, _, _ = expenses
    actor, conv = conversation(client, records)
    body = request(conv)
    first = calculate(client, actor, conv, body).json()["data"]["calculation"]
    again = calculate(client, actor, conv, body).json()["data"]["calculation"]
    assert first["id"] == again["id"] and len(messages(client, actor, conv)) == 2
    changed = {**body, "expense_context": {**body["expense_context"], "actual_amount": "601.00"}}
    assert calculate(client, actor, conv, changed).status_code == 409
    assert calculate(client, actor, conv, request(conv)).status_code == 409


def test_missing_conditions_do_not_create_a_total(expenses):
    client, records, _, _ = expenses
    actor, conv = conversation(client, records)
    body = request(conv)
    body["expense_context"] = {"kind": "training"}
    value = calculate(client, actor, conv, body).json()["data"]["calculation"]
    assert (
        value["missing_fields"] == ["quantity", "actual_amount"] and value["total_amount"] is None
    )


def test_rules_outside_selected_scope_are_not_used(expenses):
    client, records, _, _ = expenses
    actor = login(client, "bob")
    conv = client.post(
        API + "/conversations", headers=actor, json={"knowledge_base_ids": [records["restricted"]]}
    ).json()["data"]
    value = calculate(client, actor, conv, request(conv)).json()["data"]["calculation"]
    assert value["total_amount"] is None and value["items"] == []
    assert value["blocked_reasons"][0]["code"] == "RULE_UNAVAILABLE"


def test_revised_rule_hides_previous_expense_answer(expenses):
    client, records, admin, rules = expenses
    actor, conv = conversation(client, records)
    value = calculate(client, actor, conv, request(conv)).json()["data"]["calculation"]
    rule = rules["office"]
    result = client.post(
        API + f"/admin/fee-rules/{rule['id']}/suspend",
        headers=admin,
        json={"expected_revision": rule["revision"]},
    )
    assert result.status_code == 200
    assert messages(client, actor, conv)[-1]["status"] == "source_unavailable"
    assert client.get(API + f"/calculations/{value['id']}", headers=actor).status_code == 404


@pytest.mark.parametrize(
    "question,guessed_quantity,expected_quantity",
    [
        ("培训2人，实际金额6000元，请按制度核算", 2, 2),
        ("培训2人，实际申请金额6000元，请按制度核算", 2, 2),
        ("培训2人，申请报销金额6000元，请按制度核算", 2, 2),
        ("培训，实际金额6000元，请按制度核算", 2, None),
    ],
)
def test_natural_expense_proposal_needs_confirmation_and_does_not_guess(
    expenses, monkeypatch, question, guessed_quantity, expected_quantity
):
    client, records, _, _ = expenses
    actor, conv = conversation(client, records)

    async def scripted(self, inputs, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return response(
                "propose_expense_context",
                {"kind": "training", "quantity": guessed_quantity, "actual_amount": "6000"},
            )
        return response(
            "Answer",
            {
                "status": "needs_clarification",
                "basis": "expense_proposal",
                "answer": "编造核算结果999999元",
                "citations": [],
                "missing_fields": [],
            },
        )

    monkeypatch.setattr(ScriptedModel, "_agenerate", scripted)
    run, _ = submit(client, actor, conv, content=question)
    execute(client, run)
    last = messages(client, actor, conv)[-1]
    assert last["expense_proposal"]["expense_context"].get("quantity") == expected_quantity
    assert last["expense_proposal"]["expense_context"]["actual_amount"] == "6000"
    assert "999999" not in last["content"] and "calculation" not in last
