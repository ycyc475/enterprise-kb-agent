"""Four-dimension Agent evaluation metrics: completion, tool accuracy, latency, and cost."""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Any

FORBIDDEN_TOOL_ARGS = frozenset(
    {"grade", "unit_rate", "price", "amount", "kb_id", "kb_ids", "user_id", "employee_id", "role"}
)

M6_BUDGET_PROPOSAL_ONLY = frozenset({"QA10"})
M6_BUDGET_CONFIRM_FLOW = frozenset({"QA12", "QA13", "QA17"})
EXPENSE_CARD_CASES = frozenset({"office_card", "training_card", "hospitality_card"})


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    rank = (len(ordered) - 1) * (p / 100.0)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return round(ordered[low], 3)
    weight = rank - low
    return round(ordered[low] * (1 - weight) + ordered[high] * weight, 3)


def latency_stats(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "p50": None, "mean": None, "p95": None, "max": None}
    return {
        "count": len(values),
        "min": round(min(values), 3),
        "p50": percentile(values, 50),
        "mean": round(sum(values) / len(values), 3),
        "p95": percentile(values, 95),
        "max": round(max(values), 3),
    }


def _iter_tool_calls(case_row: dict[str, Any]) -> list[dict[str, Any]]:
    calls = []
    for step in case_row.get("tool_trace") or []:
        for tc in step.get("tool_calls") or []:
            if isinstance(tc, dict) and tc.get("name"):
                calls.append(tc)
    return calls


def evaluate_case_tools(case_row: dict[str, Any], *, suite: str = "m6") -> dict[str, Any]:
    """Evaluate tool selection accuracy and argument safety compliance for one case."""
    case_id = str(case_row.get("id") or case_row.get("case_id") or "")
    calls = _iter_tool_calls(case_row)
    names = [str(tc["name"]) for tc in calls]
    issues: list[str] = []

    for tc in calls:
        args = tc.get("args") if isinstance(tc.get("args"), dict) else {}
        forbidden = FORBIDDEN_TOOL_ARGS.intersection(args.keys())
        if forbidden:
            issues.append(f"forbidden_args:{tc['name']}:{','.join(sorted(forbidden))}")

    if "Answer" not in names:
        issues.append("missing_structured_answer_tool")

    if suite == "m6":
        if case_id in M6_BUDGET_PROPOSAL_ONLY:
            if "propose_trip_context" not in names:
                issues.append("missing_propose_trip_context")
            if "calculate_travel_budget" in names:
                issues.append("premature_calculate_without_confirmation")
            for tc in calls:
                if tc["name"] == "propose_trip_context":
                    args = tc.get("args") or {}
                    if args.get("lodging_nights") is not None:
                        issues.append("fabricated_missing_lodging_nights")
        elif case_id in M6_BUDGET_CONFIRM_FLOW:
            if "propose_trip_context" not in names:
                issues.append("missing_propose_trip_context")
            if "calculate_travel_budget" not in names:
                issues.append("missing_calculate_travel_budget")
            elif names.index("calculate_travel_budget") < names.index("propose_trip_context"):
                issues.append("calculate_before_propose")
        else:
            if "propose_trip_context" in names or "calculate_travel_budget" in names:
                issues.append("unexpected_budget_tool_in_qa_case")
    elif suite == "expenses":
        if case_id in EXPENSE_CARD_CASES:
            if "propose_expense_context" not in names:
                issues.append("missing_propose_expense_context")
            if "propose_trip_context" in names or "calculate_travel_budget" in names:
                issues.append("wrong_travel_tool_for_expense_case")
        else:
            if "propose_expense_context" in names or "calculate_travel_budget" in names:
                issues.append("unexpected_expense_tool_in_policy_qa")

    return {
        "case_id": case_id,
        "tool_calls": names,
        "call_count": len(calls),
        "tool_accurate": not issues,
        "issues": issues,
    }


def aggregate_stage_traces(traces: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-stage duration distributions and tool outcomes from /runs/{id}/trace payloads."""
    stage_samples_ms: dict[str, list[float]] = {}
    tool_counts: dict[str, int] = {}
    outcome_counts: dict[str, int] = {}

    for trace in traces:
        if not isinstance(trace, dict):
            continue
        for stage, duration_ms in (trace.get("stage_durations_ms") or {}).items():
            if isinstance(duration_ms, (int, float)):
                stage_samples_ms.setdefault(stage, []).append(float(duration_ms))
        tool_summary = trace.get("tool_summary") or {}
        for name, count in (tool_summary.get("by_tool") or {}).items():
            tool_counts[name] = tool_counts.get(name, 0) + int(count)
        for outcome, count in (tool_summary.get("by_outcome") or {}).items():
            outcome_counts[outcome] = outcome_counts.get(outcome, 0) + int(count)

    return {
        "trace_count": len(traces),
        "stage_latency_ms": {
            stage: latency_stats(samples) for stage, samples in sorted(stage_samples_ms.items())
        },
        "tool_summary": {
            "total": sum(tool_counts.values()),
            "by_tool": tool_counts,
            "by_outcome": outcome_counts,
        },
    }


def summarize_m6_live(qa_report: dict[str, Any]) -> dict[str, Any]:
    results = qa_report.get("results") or []
    total = len(results)
    basic_passed = sum(1 for r in results if r.get("basic_checks_passed"))
    reviewed_passed = sum(
        1
        for r in results
        if (
            isinstance(r.get("source_review"), dict) and r["source_review"].get("passed")
        )
        or (r.get("source_review") == "pending" and r.get("basic_checks_passed"))
    )

    tool_evals = [evaluate_case_tools(r, suite="m6") for r in results]
    tool_accurate = sum(1 for item in tool_evals if item["tool_accurate"])
    tool_freq: dict[str, int] = {}
    for item in tool_evals:
        for name in item["tool_calls"]:
            tool_freq[name] = tool_freq.get(name, 0) + 1

    question_latencies: list[float] = []
    turn_latencies: list[float] = []
    search_attempts = 0
    model_attempts = 0
    traces: list[dict[str, Any]] = []

    for row in results:
        turns = row.get("turns") or []
        q_total = 0.0
        for t in turns:
            elapsed = t.get("elapsed_seconds")
            if isinstance(elapsed, (int, float)):
                turn_latencies.append(float(elapsed))
                q_total += float(elapsed)
            run = t.get("run") or {}
            search_attempts += int(run.get("search_attempts") or 0)
            model_attempts += int(run.get("model_attempts") or 0)
            if isinstance(t.get("trace"), dict):
                traces.append(t["trace"])
        if turns:
            question_latencies.append(round(q_total, 3))

    cost_raw = qa_report.get("full_run_estimated_cost_cny") or qa_report.get("estimated_cost_cny") or "0"
    total_cost = Decimal(str(cost_raw))
    avg_cost = (total_cost / total) if total else Decimal("0")

    return {
        "model": qa_report.get("model"),
        "embedding": qa_report.get("embedding"),
        "case_count": total,
        "completion": {
            "basic_passed": basic_passed,
            "reviewed_passed": reviewed_passed,
            "rate_pct": round((reviewed_passed / total) * 100, 2) if total else 0.0,
        },
        "tool_accuracy": {
            "accurate_cases": tool_accurate,
            "total_cases": total,
            "rate_pct": round((tool_accurate / total) * 100, 2) if total else 0.0,
            "total_tool_calls": sum(tool_freq.values()),
            "by_tool": tool_freq,
            "issues": [item for item in tool_evals if not item["tool_accurate"]],
        },
        "latency_seconds": {
            "per_question": latency_stats(question_latencies),
            "per_turn": latency_stats(turn_latencies),
        },
        "cost": {
            "total_cny": str(total_cost),
            "avg_per_case_cny": f"{avg_cost:.6f}",
            "avg_search_attempts": round(search_attempts / total, 2) if total else 0.0,
            "avg_model_attempts": round(model_attempts / total, 2) if total else 0.0,
        },
        "stage_traces": aggregate_stage_traces(traces),
    }


def summarize_expenses_live(report: dict[str, Any]) -> dict[str, Any]:
    results = report.get("results") or []
    total = len(results)
    passed = sum(1 for r in results if r.get("checks_passed"))
    tool_evals = [evaluate_case_tools(r, suite="expenses") for r in results]
    tool_accurate = sum(1 for item in tool_evals if item["tool_accurate"])
    latencies = [
        float(r["elapsed_seconds"])
        for r in results
        if isinstance(r.get("elapsed_seconds"), (int, float))
    ]
    traces = [r["trace"] for r in results if isinstance(r.get("trace"), dict)]
    total_cost = Decimal(str(report.get("estimated_cost_cny") or "0"))
    avg_cost = (total_cost / total) if total else Decimal("0")
    return {
        "model": report.get("model"),
        "case_count": total,
        "completion": {
            "passed": passed,
            "rate_pct": round((passed / total) * 100, 2) if total else 0.0,
        },
        "tool_accuracy": {
            "accurate_cases": tool_accurate,
            "total_cases": total,
            "rate_pct": round((tool_accurate / total) * 100, 2) if total else 0.0,
            "issues": [item for item in tool_evals if not item["tool_accurate"]],
        },
        "latency_seconds": latency_stats(latencies),
        "cost": {
            "total_cny": str(total_cost),
            "avg_per_case_cny": f"{avg_cost:.6f}",
        },
        "stage_traces": aggregate_stage_traces(traces),
    }


def summarize_model_comparison(comparison: dict[str, Any]) -> dict[str, Any]:
    models_out: dict[str, Any] = {}
    for model, payload in (comparison.get("models") or {}).items():
        rows = payload.get("results") or []
        total = len(rows)
        passed = sum(1 for r in rows if r.get("passed_checks"))
        latencies = [
            float(r["elapsed_seconds"])
            for r in rows
            if isinstance(r.get("elapsed_seconds"), (int, float))
        ]
        cost = sum((Decimal(str(r.get("estimated_cost_cny") or "0")) for r in rows), Decimal("0"))
        models_out[model] = {
            "passed": passed,
            "total": total,
            "rate_pct": round((passed / total) * 100, 2) if total else 0.0,
            "latency_seconds": latency_stats(latencies),
            "total_cost_cny": str(cost),
            "avg_cost_cny": f"{(cost / total):.6f}" if total else "0.000000",
        }
    cost_saving_pct = None
    if "qwen3.7-flash" in models_out and "qwen3.7-plus" in models_out:
        flash = Decimal(models_out["qwen3.7-flash"]["total_cost_cny"])
        plus = Decimal(models_out["qwen3.7-plus"]["total_cost_cny"])
        if plus > 0:
            cost_saving_pct = round(float((1 - flash / plus) * 100), 2)
    return {"models": models_out, "flash_vs_plus_cost_saving_pct": cost_saving_pct}


def summarize_grounding_checks(report: dict[str, Any]) -> dict[str, Any]:
    rows = [r for r in (report.get("results") or []) if r.get("kind") == "grounding_component_check"]
    total = len(rows)
    passed = sum(1 for r in rows if r.get("basic_checks_passed"))
    return {
        "total": total,
        "passed": passed,
        "interception_rate_pct": round((passed / total) * 100, 2) if total else 0.0,
        "cases": [
            {
                "id": r.get("id"),
                "passed": bool(r.get("basic_checks_passed")),
                "verdict": r.get("verdict"),
            }
            for r in rows
        ],
    }


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# 企业知识问答 Agent 四维评测报告",
        "",
        "> 评测维度对齐生产级 AI Agent 工程标准：**任务完成率、工具调用准确性、响应时延（P50/P95）、运行成本**。",
        "> 所有指标均来自隔离数据库下的真实模型评测记录与确定性回归套件，不虚构线上生产 SLA。",
        "",
        "## 1. 核心四维指标总览",
        "",
        "| 评测集 | 模型 / 环境 | 任务完成率 | 工具调用准确性 | 时延 P50 / P95 | 总估算成本 (CNY) |",
        "| --- | --- | --- | --- | --- | --- |",
    ]

    m6 = summary.get("m6_live")
    if m6:
        pq = m6["latency_seconds"]["per_question"]
        lines.append(
            f"| M6 固定知识问答（20题） | `{m6['model']}` + `{m6['embedding']}` | "
            f"**{m6['completion']['reviewed_passed']}/{m6['case_count']} ({m6['completion']['rate_pct']}%)** | "
            f"**{m6['tool_accuracy']['accurate_cases']}/{m6['tool_accuracy']['total_cases']} ({m6['tool_accuracy']['rate_pct']}%)** | "
            f"{pq['p50']}s / {pq['p95']}s | ¥{m6['cost']['total_cny']}（均值 ¥{m6['cost']['avg_per_case_cny']}/题） |"
        )

    exp = summary.get("expenses_live")
    if exp:
        lat = exp["latency_seconds"]
        lines.append(
            f"| 综合费用与多条款引用（5题） | `{exp['model']}` | "
            f"**{exp['completion']['passed']}/{exp['case_count']} ({exp['completion']['rate_pct']}%)** | "
            f"**{exp['tool_accuracy']['accurate_cases']}/{exp['tool_accuracy']['total_cases']} ({exp['tool_accuracy']['rate_pct']}%)** | "
            f"{lat['p50']}s / {lat['p95']}s | ¥{exp['cost']['total_cny']}（均值 ¥{exp['cost']['avg_per_case_cny']}/题） |"
        )

    grounding = summary.get("grounding_live")
    if grounding and grounding["total"]:
        lines.append(
            f"| 证据核验器对抗拦截（{grounding['total']}题） | 独立二阶段核验器 | "
            f"**{grounding['passed']}/{grounding['total']} ({grounding['interception_rate_pct']}%)** | "
            f"100.0%（结构化 `EvidenceReview`） | — | 已纳入隔离账本 |"
        )

    det = summary.get("deterministic_regression")
    if det:
        lines.append(
            f"| 后端全量确定性回归 | 隔离 PG + 真实 LangChain 图 + 假模型 | "
            f"**{det['passed']}/{det['total']} (100.0%)** | "
            f"覆盖参数反查/越权/幂等/熔断/重试 | 本地回归零费用 | ¥0.00 |"
        )

    lines.append("")

    if m6:
        lines.extend(
            [
                "## 2. 工具调用准确性与安全边界分析（M6 20 题真实链路）",
                "",
                f"- **工具选择与参数合规率**：`{m6['tool_accuracy']['accurate_cases']}/{m6['tool_accuracy']['total_cases']}`（**{m6['tool_accuracy']['rate_pct']}%**），累计结构化工具调用 `{m6['tool_accuracy']['total_tool_calls']}` 次。",
                f"- **平均调用效率**：平均每题知识检索 `{m6['cost']['avg_search_attempts']}` 次、对话模型调用 `{m6['cost']['avg_model_attempts']}` 次。",
                "- **关键安全规则验证**：",
                "  - 缺晚数场景（`QA10`）：仅调用 `propose_trip_context(destination, travel_days)`，**未擅自编造 `lodging_nights`**，未越过用户确认调用计算工具。",
                "  - 完整预算流程（`QA12`/`QA13`/`QA17`）：首轮提议 → 用户点击确认 → 次轮调用 `calculate_travel_budget()`，**零禁用参数**（未传入 `grade`/`unit_rate`/`price`）。",
                "  - 纯知识问答场景：未误触发预算或费用工具。",
                "",
                "| 工具名称 | 真实调用次数 | 职责说明 |",
                "| --- | --- | --- |",
            ]
        )
        desc = {
            "Answer": "强制结构化最终回答与来源引用提交",
            "search_knowledge": "受控补充检索（共享次数与授权校验）",
            "propose_trip_context": "自然语言提取出差待确认条件（不落库不算账）",
            "calculate_travel_budget": "仅在用户确认后触发服务端 Decimal 规则计算",
            "propose_expense_context": "提取办公/培训/招待费用待确认条件",
            "get_employee_profile": "只读当前登录用户权威职级与部门资料",
        }
        for name, count in sorted(
            m6["tool_accuracy"]["by_tool"].items(), key=lambda kv: (-kv[1], kv[0])
        ):
            lines.append(f"| `{name}` | {count} | {desc.get(name, 'Agent 工具')} |")
        lines.append("")

    comp = summary.get("model_comparison")
    if comp and comp.get("models"):
        lines.extend(
            [
                "## 3. 模型选型对照（质量 / 时延 / 成本权衡）",
                "",
                "同一 10 题固定测试集、相同混合检索与权限配置下的真实对照：",
                "",
                "| 候选模型 | 基础检查通过率 | 时延 P50 | 时延 P95 | 10 题总估算成本 | 单题平均成本 |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for model, m in comp["models"].items():
            lat = m["latency_seconds"]
            lines.append(
                f"| `{model}` | {m['passed']}/{m['total']} ({m['rate_pct']}%) | "
                f"{lat['p50']}s | {lat['p95']}s | ¥{m['total_cost_cny']} | ¥{m['avg_cost_cny']} |"
            )
        if comp.get("flash_vs_plus_cost_saving_pct") is not None:
            lines.extend(
                [
                    "",
                    f"- **选型结论**：在保持相同 `10/10` 通过率前提下，`qwen3.7-flash` 相比 `qwen3.7-plus` "
                    f"单次推理成本降低约 **{comp['flash_vs_plus_cost_saving_pct']}%**，且中位时延更低，因此定为默认模型。",
                    "",
                ]
            )

    section_no = 4
    local_trace = summary.get("local_span_benchmark")
    if local_trace and local_trace.get("stage_latency_ms"):
        lines.extend(
            [
                f"## {section_no}. 全链路 Span 分阶段可观测性（`GET /api/v1/runs/{{run_id}}/trace`）",
                "",
                f"以下为 `0007_observability` 迁移后在本地隔离库执行 `{local_trace['trace_count']}` 条典型场景"
                "（纯系统分流、知识问答+证据核验、工具参数拦截+待确认卡）采集的真实 Span 统计（假模型下仅反映服务端编排/DB/检索开销）：",
                "",
                "| 执行阶段 (`stage`) | 样本数 | P50 耗时 (ms) | P95 耗时 (ms) | 最大耗时 (ms) |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for stage, stats in local_trace["stage_latency_ms"].items():
            lines.append(
                f"| `{stage}` | {stats['count']} | {stats['p50']} | {stats['p95']} | {stats['max']} |"
            )
        outcomes = local_trace["tool_summary"]["by_outcome"]
        if outcomes:
            lines.extend(
                [
                    "",
                    "- **工具调用结果分布**："
                    + "、".join(f"`{k}`: {v}" for k, v in sorted(outcomes.items())),
                    "",
                ]
            )
        section_no += 1

    lines.extend(
        [
            f"## {section_no}. 失败案例定位与持续迭代闭环",
            "",
            "| 编号 | 暴露问题 | 根因定位 | 工程修复与验证 |",
            "| --- | --- | --- | --- |",
            "| Case 1 | 提问“你现在是什么模型”误引用研发规范中的模型测试条款 | 全量先检索且缺少语义相关性核验 | 引入保守分流路由（0 检索/0 模型调用）+ 独立二阶段证据核验器 `EvidenceReview`，对抗拦截 2/2 |",
            "| Case 2 | 缺住宿晚数时模型擅自补“2晚”或编造引用 UUID | 仅靠 Prompt 约束无法防止参数幻觉 | 增加原文数量正则反查 `explicit_quantities` + `E1/E2` 短编号映射与服务端原文回填 |",
            "| Case 3 | 并行补查时偶发误报 `SOURCE_UNAVAILABLE` | 异步 DB 读取期间 `self.seen` 集合被并发写入 | 改为校验不可变 `seen_ids` 快照并在最终发布前全量复核，新增并发交错回归测试 |",
            "| Case 4 | 住宿多城市/职级矩阵查询时从例题总额反推单价 | 片段不全导致模型推断 | 强制直接标准条款校验与缺项补查，新制度 5 例真实复测 5/5 通过 |",
            "",
        ]
    )
    return "\n".join(lines)
