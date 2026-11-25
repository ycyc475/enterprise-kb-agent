from app.core.evaluation import (
    aggregate_stage_traces,
    evaluate_case_tools,
    render_markdown,
    summarize_m6_live,
    summarize_model_comparison,
)
from tests.test_m3 import execute, submit
from tests.test_m3 import setup_chat as setup_chat

API = "/api/v1"


def test_tool_accuracy_catches_premature_calc_fabricated_nights_and_forbidden_args():
    good_qa10 = {
        "id": "QA10",
        "tool_trace": [
            {
                "tool_calls": [
                    {"name": "propose_trip_context", "args": {"destination": "北京", "travel_days": 3}},
                    {"name": "Answer", "args": {"status": "needs_clarification"}},
                ]
            }
        ],
    }
    assert evaluate_case_tools(good_qa10, suite="m6")["tool_accurate"] is True

    bad_qa10 = {
        "id": "QA10",
        "tool_trace": [
            {
                "tool_calls": [
                    {
                        "name": "propose_trip_context",
                        "args": {"destination": "北京", "travel_days": 3, "lodging_nights": 2, "grade": "S2"},
                    },
                    {"name": "calculate_travel_budget", "args": {}},
                    {"name": "Answer", "args": {"status": "answered"}},
                ]
            }
        ],
    }
    result = evaluate_case_tools(bad_qa10, suite="m6")
    assert result["tool_accurate"] is False
    assert "fabricated_missing_lodging_nights" in result["issues"]
    assert "premature_calculate_without_confirmation" in result["issues"]
    assert any(i.startswith("forbidden_args:propose_trip_context:grade") for i in result["issues"])


def test_four_dimension_summary_and_markdown_rendering():
    sample_qa = {
        "model": "qwen3.7-flash",
        "embedding": "text-embedding-v4/1024/v1",
        "full_run_estimated_cost_cny": "0.004000",
        "results": [
            {
                "id": "QA01",
                "basic_checks_passed": True,
                "source_review": {"passed": True},
                "turns": [
                    {
                        "elapsed_seconds": 3.0,
                        "run": {"search_attempts": 1, "model_attempts": 1},
                        "trace": {
                            "stage_durations_ms": {"routing": 1, "retrieval": 120, "llm": 1800},
                            "tool_summary": {"by_tool": {"Answer": 1}, "by_outcome": {"ok": 1}},
                        },
                    }
                ],
                "tool_trace": [{"tool_calls": [{"name": "Answer", "args": {}}]}],
            },
            {
                "id": "QA12",
                "basic_checks_passed": True,
                "source_review": {"passed": True},
                "turns": [
                    {"elapsed_seconds": 4.0, "run": {"search_attempts": 1, "model_attempts": 2}},
                    {"elapsed_seconds": 5.0, "run": {"search_attempts": 1, "model_attempts": 2}},
                ],
                "tool_trace": [
                    {
                        "tool_calls": [
                            {"name": "propose_trip_context", "args": {"destination": "北京"}},
                            {"name": "Answer", "args": {}},
                            {"name": "calculate_travel_budget", "args": {}},
                            {"name": "Answer", "args": {}},
                        ]
                    }
                ],
            },
        ],
    }
    m6 = summarize_m6_live(sample_qa)
    assert m6["completion"]["rate_pct"] == 100.0
    assert m6["tool_accuracy"]["rate_pct"] == 100.0
    assert m6["latency_seconds"]["per_question"]["p50"] == 6.0
    assert m6["cost"]["avg_per_case_cny"] == "0.002000"
    assert m6["stage_traces"]["stage_latency_ms"]["retrieval"]["p50"] == 120.0

    comp = summarize_model_comparison(
        {
            "models": {
                "qwen3.7-flash": {
                    "results": [
                        {"passed_checks": True, "elapsed_seconds": 3.0, "estimated_cost_cny": "0.001"}
                    ]
                },
                "qwen3.7-plus": {
                    "results": [
                        {"passed_checks": True, "elapsed_seconds": 4.5, "estimated_cost_cny": "0.010"}
                    ]
                },
            }
        }
    )
    assert comp["flash_vs_plus_cost_saving_pct"] == 90.0

    md = render_markdown({"m6_live": m6, "model_comparison": comp})
    assert "四维评测报告" in md and "90.0%" in md and "propose_trip_context" in md


def test_live_trace_feeds_stage_aggregation(setup_chat):
    client, _, _, _, actor, conv, _ = setup_chat
    traces = []
    for question in ("你现在是什么模型？", "北京出差住宿标准是多少？"):
        run, _ = submit(client, actor, conv, content=question)
        execute(client, run)
        traces.append(client.get(API + f"/runs/{run['id']}/trace", headers=actor).json()["data"])
    agg = aggregate_stage_traces(traces)
    assert agg["trace_count"] == 2
    assert agg["stage_latency_ms"]["routing"]["count"] == 2
    assert agg["stage_latency_ms"]["retrieval"]["count"] == 1
    assert agg["stage_latency_ms"]["evidence_review"]["count"] == 1
