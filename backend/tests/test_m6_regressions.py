"""Regression cases for failures observed during the live acceptance review."""

import uuid

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from tests.m6_support import API
from tests.test_m3 import ScriptedModel, execute, messages, submit
from tests.test_m4 import conversation
from tests.test_m6 import acceptance as acceptance


def response(name, args):
    return ChatResult(
        generations=[
            ChatGeneration(
                message=AIMessage(
                    content="",
                    tool_calls=[{"name": name, "args": args, "id": uuid.uuid4().hex}],
                    usage_metadata={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
                )
            )
        ]
    )


def test_malformed_answer_is_corrected_within_metered_model_budget(acceptance, monkeypatch):
    client, records, _, _, _, _ = acceptance
    actor, conv = conversation(client, records)
    original = ScriptedModel._agenerate

    async def scripted(self, inputs, **kwargs):
        if self.calls == 0:
            self.calls += 1
            return response("Answer", {"status": "not-a-valid-status", "answer": "bad format"})
        return await original(self, inputs, **kwargs)

    monkeypatch.setattr(ScriptedModel, "_agenerate", scripted)
    run, _ = submit(client, actor, conv)
    execute(client, run)
    actual = client.get(API + f"/runs/{run['id']}", headers=actor).json()["data"]
    assert actual["status"] == "completed" and actual["model_attempts"] == 3
    assert messages(client, actor, conv)[-1]["citations"]


@pytest.mark.parametrize(
    "question,nights",
    [
        ("我去北京出差三天，请估算预算", None),
        ("我去北京出差三天住宿两晚，请估算预算", 2),
        ("我去北京出差1天，住宿0晚，请估算预算", 0),
    ],
)
def test_model_guessed_nights_never_fill_an_unmentioned_quantity(
    acceptance, monkeypatch, question, nights
):
    client, records, _, _, _, _ = acceptance
    actor, conv = conversation(client, records)
    days = 1 if nights == 0 else 3

    async def scripted(self, inputs, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return response(
                "propose_trip_context",
                {
                    "destination": "北京",
                    "travel_days": days,
                    "lodging_nights": nights if nights is not None else 2,
                },
            )
        return response(
            "Answer",
            {
                "status": "needs_clarification",
                "basis": "trip_proposal",
                "answer": "请确认",
                "citations": [],
                "missing_fields": [],
            },
        )

    monkeypatch.setattr(ScriptedModel, "_agenerate", scripted)
    run, _ = submit(client, actor, conv, content=question)
    execute(client, run)
    last = messages(client, actor, conv)[-1]
    assert last["trip_proposal"]["trip_context"].get("lodging_nights") == nights
    assert ("lodging_nights" in last["missing_fields"]) == (nights is None)
    assert "calculation" not in last


@pytest.mark.parametrize("forged", [False, True])
def test_budget_guide_retains_verified_steps_without_model_amounts(acceptance, monkeypatch, forged):
    client, records, _, _, chunks, _ = acceptance
    actor, conv = conversation(client, records)
    conv = client.patch(
        API + f"/conversations/{conv['id']}/trip-context",
        headers=actor,
        json={
            "expected_revision": 1,
            "trip_context": {"destination": "北京", "travel_days": 3, "lodging_nights": 2},
        },
    ).json()["data"]
    steps = [
        c
        for c in chunks["PROCESS"]
        if "出行过程中保存" in c["content"] or "准备出差申请" in c["content"]
    ]
    assert len(steps) == 2

    async def scripted(self, inputs, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return response(
                "search_knowledge", {"query": "出行前申请批准 返回后 行程 凭证 人工审核"}
            )
        if self.calls == 2:
            return response("calculate_travel_budget", {})
        return response(
            "Answer",
            {
                "status": "answered",
                "basis": "calculation",
                "answer": "模型编造预算999999元",
                "citations": [
                    {
                        "chunk_id": c["chunk_id"],
                        "quote": "未经原文支持的步骤" if forged else c["content"],
                    }
                    for c in steps
                ],
                "missing_fields": [],
            },
        )

    monkeypatch.setattr(ScriptedModel, "_agenerate", scripted)
    run, _ = submit(
        client,
        actor,
        conv,
        content="请计算预算并整理办理指南",
        budget_confirmed_revision=conv["context_revision"],
        budget_profile_revision=1,
    )
    execute(client, run)
    actual = client.get(API + f"/runs/{run['id']}", headers=actor).json()["data"]
    last = messages(client, actor, conv)[-1]
    assert "999999" not in last["content"]
    if forged:
        assert actual["stop_reason"] == "INVALID_CITATION" and "calculation" not in last
    else:
        assert last["calculation"]["total_amount"] == "840.00"
        assert "批准" in last["content"] and "人工审核" in last["content"]
        assert len(last["citations"]) == 4
