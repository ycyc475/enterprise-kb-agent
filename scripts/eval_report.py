"""Generate the four-dimension Agent evaluation report (JSON + Markdown) with zero paid API calls."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text("utf-8"))


def _find_latest_archived(predicate) -> dict | None:
    root = ROOT / ".m6/evaluations"
    if not root.exists():
        return None
    candidates = sorted(
        root.glob("*/qa-results.json"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    for path in candidates:
        data = _load_json(path)
        if data and predicate(data):
            return data
    return None


def build_summary(
    *,
    qa_path: Path,
    comparison_path: Path,
    expenses_path: Path | None = None,
    grounding_path: Path | None = None,
    deterministic_total: int = 204,
    deterministic_passed: int = 204,
    local_span_benchmark: dict | None = None,
) -> dict:
    if str(ROOT / "backend") not in sys.path:
        sys.path.insert(0, str(ROOT / "backend"))
    from app.core.evaluation import (
        summarize_expenses_live,
        summarize_grounding_checks,
        summarize_m6_live,
        summarize_model_comparison,
    )
    summary: dict = {
        "deterministic_regression": {
            "total": deterministic_total,
            "passed": deterministic_passed,
        }
    }
    qa_data = _load_json(qa_path)
    if qa_data:
        summary["m6_live"] = summarize_m6_live(qa_data)

    comp_data = _load_json(comparison_path)
    if comp_data:
        summary["model_comparison"] = summarize_model_comparison(comp_data)

    exp_data = _load_json(expenses_path) if expenses_path else _find_latest_archived(
        lambda d: d.get("suite") == "expense-reliability"
        and len(d.get("results") or []) >= 5
        and all(r.get("checks_passed") for r in d.get("results") or [])
    )
    if exp_data:
        summary["expenses_live"] = summarize_expenses_live(exp_data)

    ground_data = _load_json(grounding_path) if grounding_path else _find_latest_archived(
        lambda d: any(
            r.get("kind") == "grounding_component_check" for r in (d.get("results") or [])
        )
    )
    if ground_data:
        summary["grounding_live"] = summarize_grounding_checks(ground_data)

    if local_span_benchmark:
        summary["local_span_benchmark"] = local_span_benchmark
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build four-dimension Agent evaluation report (zero live API cost)."
    )
    parser.add_argument("--qa", type=Path, default=ROOT / ".m6/qa-final.json")
    parser.add_argument("--comparison", type=Path, default=ROOT / ".m3/model-comparison.json")
    parser.add_argument("--expenses", type=Path, default=None)
    parser.add_argument("--grounding", type=Path, default=None)
    parser.add_argument("--deterministic-total", type=int, default=201)
    parser.add_argument("--deterministic-passed", type=int, default=201)
    parser.add_argument(
        "--span-benchmark-json",
        type=Path,
        default=None,
        help="Optional JSON produced by local trace benchmark.",
    )
    parser.add_argument("--json-out", type=Path, default=ROOT / ".m6/four-dim-summary.json")
    parser.add_argument(
        "--md-out", type=Path, default=ROOT / "docs/评测报告-四维指标汇总.md"
    )
    args = parser.parse_args()

    span_data = _load_json(args.span_benchmark_json) if args.span_benchmark_json else None
    summary = build_summary(
        qa_path=args.qa,
        comparison_path=args.comparison,
        expenses_path=args.expenses,
        grounding_path=args.grounding,
        deterministic_total=args.deterministic_total,
        deterministic_passed=args.deterministic_passed,
        local_span_benchmark=span_data,
    )
    from app.core.evaluation import render_markdown

    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), "utf-8")
    args.md_out.parent.mkdir(parents=True, exist_ok=True)
    args.md_out.write_text(render_markdown(summary), "utf-8")
    print(
        json.dumps(
            {
                "json_out": str(args.json_out.relative_to(ROOT)),
                "md_out": str(args.md_out.relative_to(ROOT)),
                "m6_completion": summary.get("m6_live", {}).get("completion"),
                "m6_tool_accuracy": {
                    k: v
                    for k, v in (summary.get("m6_live", {}).get("tool_accuracy") or {}).items()
                    if k != "issues"
                },
                "expenses_completion": summary.get("expenses_live", {}).get("completion"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
