import json
import logging

from app.core.telemetry import JsonLogFormatter, log_event, run_id_var, trace_id_var
from tests.conftest import login
from tests.test_m3 import ScriptedModel, execute, submit
from tests.test_m3 import setup_chat as setup_chat
from tests.test_m6_regressions import response

API = "/api/v1"


def test_json_log_formatter_redacts_sensitive_fields_and_propagates_trace():
    formatter = JsonLogFormatter()
    captured = []

    class Collector(logging.Handler):
        def emit(self, record):
            captured.append(json.loads(formatter.format(record)))

    logger = logging.getLogger("app.test_telemetry")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = Collector()
    logger.addHandler(handler)
    t_tok = trace_id_var.set("trace-abc")
    r_tok = run_id_var.set("run-123")
    try:
        log_event(
            "agent.tool_call",
            logger_name="app.test_telemetry",
            stage="tool",
            tool_name="search_knowledge",
            duration_ms=42,
            api_key="sk-secret",
            query="北京住宿标准",
            content="敏感制度全文",
            nested={"password": "123", "safe_count": 3},
        )
    finally:
        run_id_var.reset(r_tok)
        trace_id_var.reset(t_tok)
        logger.removeHandler(handler)

    assert len(captured) == 1
    entry = captured[0]
    assert entry["event"] == "agent.tool_call"
    assert entry["trace_id"] == "trace-abc"
    assert entry["run_id"] == "run-123"
    assert entry["duration_ms"] == 42
    assert entry["tool_name"] == "search_knowledge"
    assert entry["nested"] == {"safe_count": 3}
    for forbidden in ("api_key", "query", "content", "sk-secret", "北京", "敏感制度全文"):
        assert forbidden not in json.dumps(entry, ensure_ascii=False)


def test_run_trace_exposes_stage_latencies_tokens_and_hides_sensitive_text(setup_chat):
    client, _, _, _, actor, conv, _ = setup_chat
    run, _ = submit(client, actor, conv, content="北京出差住宿标准是多少？")
    execute(client, run)

    events_resp = client.get(API + f"/runs/{run['id']}/events", headers=actor)
    assert events_resp.status_code == 200
    events = events_resp.json()["data"]["items"]
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert "北京" not in json.dumps(events, ensure_ascii=False)
    assert any(
        (e.get("meta") or {}).get("stage") == "retrieval" and isinstance(e.get("duration_ms"), int)
        for e in events
    )

    trace_resp = client.get(API + f"/runs/{run['id']}/trace", headers=actor)
    assert trace_resp.status_code == 200
    trace = trace_resp.json()["data"]
    assert trace["run_id"] == run["id"]
    assert trace["status"] == "completed"
    assert isinstance(trace["total_duration_ms"], int) and trace["total_duration_ms"] >= 0
    for stage in ("routing", "retrieval", "llm", "evidence_review"):
        assert stage in trace["stage_counts"] and trace["stage_counts"][stage] >= 1
        assert isinstance(trace["stage_durations_ms"][stage], int)
    assert trace["token_usage"]["requests"] >= 2
    assert trace["token_usage"]["input_tokens"] > 0
    assert trace["token_usage"]["output_tokens"] > 0
    assert "北京" not in json.dumps(trace, ensure_ascii=False)

    # Other users (including admin) cannot read private run traces.
    admin = login(client, "admin")
    assert client.get(API + f"/runs/{run['id']}/trace", headers=admin).status_code == 404


def test_run_trace_tracks_tool_outcomes_and_validation_rejections(setup_chat, monkeypatch):
    client, _, _, _, actor, conv, _ = setup_chat

    async def scripted(self, inputs, **kwargs):
        self.calls += 1
        if self.calls == 1:
            # Destination not literally in question -> triggers validation_rejected.
            return response(
                "propose_trip_context",
                {"destination": "北京市", "travel_days": 3, "lodging_nights": 2},
            )
        if self.calls == 2:
            # Unconfirmed budget call -> triggers needs_confirmation.
            return response("calculate_travel_budget", {})
        return response(
            "Answer",
            {
                "status": "needs_clarification",
                "basis": "trip_proposal",
                "answer": "请确认出差条件。",
                "citations": [],
                "missing_fields": ["destination"],
            },
        )

    monkeypatch.setattr(ScriptedModel, "_agenerate", scripted)
    run, _ = submit(client, actor, conv, content="我去北京出差3天住2晚，帮我算预算")
    execute(client, run)

    trace = client.get(API + f"/runs/{run['id']}/trace", headers=actor).json()["data"]
    summary = trace["tool_summary"]
    assert summary["by_tool"].get("propose_trip_context") == 1
    assert summary["by_tool"].get("calculate_travel_budget") == 1
    assert summary["by_outcome"].get("validation_rejected", 0) >= 1
    assert summary["by_outcome"].get("needs_confirmation", 0) >= 1
    assert "北京" not in json.dumps(trace, ensure_ascii=False)


def test_routed_system_question_trace_has_zero_retrieval_and_zero_model_calls(setup_chat):
    client, _, _, _, actor, conv, _ = setup_chat
    run, _ = submit(client, actor, conv, content="你现在是什么模型？")
    execute(client, run)

    trace = client.get(API + f"/runs/{run['id']}/trace", headers=actor).json()["data"]
    assert trace["status"] == "completed"
    assert trace["attempts"] == {"search": 0, "model": 0, "tool": 0}
    assert trace["stage_counts"] == {"routing": 1}
    assert trace["token_usage"]["requests"] == 0
