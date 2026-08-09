import argparse
import json
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace

from app.agent.prompts import AGENT_PROMPT_VERSION
from app.core.config import DEEPSEEK_MODEL
from app.integrations.deepseek import generate_itinerary_with_tools
from evals.cases import load_itinerary_cases
from evals.itinerary_evaluator import evaluate_itinerary
from evals.reporting import summarize_results


def _parse_arguments():
    parser = argparse.ArgumentParser(
        description=(
            "Run TravelMind itinerary evaluations using live DeepSeek "
            "and AMap APIs."
        )
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Confirm that paid/network API calls are allowed.",
    )
    parser.add_argument(
        "--suite",
        choices=("smoke", "matrix"),
        default="smoke",
        help=(
            "Run the one-case smoke suite or the complete paid matrix. "
            "Defaults to smoke for cost safety."
        ),
    )
    parser.add_argument(
        "--case",
        dest="case_id",
        help="Run only one case_id instead of the full case set.",
    )
    parser.add_argument(
        "--tool-agent",
        action="store_true",
        help="Deprecated compatibility flag; the Agent is now the default.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optionally save the JSON report to this path.",
    )
    return parser.parse_args()


def _runtime_metrics(
    started_at: float,
    usage_trace: list[dict],
    agent_trace: list[dict],
) -> dict:
    return {
        "duration_seconds": round(perf_counter() - started_at, 2),
        "model_turns": len(usage_trace),
        "input_tokens": sum(
            int(usage.get("input_tokens", 0))
            for usage in usage_trace
        ),
        "output_tokens": sum(
            int(usage.get("output_tokens", 0))
            for usage in usage_trace
        ),
        "tool_attempts": len(agent_trace),
        "tool_rejections": sum(
            event.get("status") == "rejected"
            for event in agent_trace
        ),
        "tool_failures": sum(
            event.get("status") == "failed"
            for event in agent_trace
        ),
    }


# === 单案例执行：隔离模型异常并保留完整成本与工具轨迹 ===
# 流程：案例 → Agent/高德 → 确定性评分 → 运行指标 → 独立结果
def run_evaluation_case(
    case,
    generator=generate_itinerary_with_tools,
) -> dict:
    trip = SimpleNamespace(**case.trip_data())
    agent_trace = []
    agent_quality_trace = []
    usage_trace = []
    started_at = perf_counter()

    try:
        itinerary = generator(
            trip,
            on_tool_result=agent_trace.append,
            on_quality_result=agent_quality_trace.append,
            on_model_usage=usage_trace.append,
        )
        evaluation = evaluate_itinerary(trip, itinerary)
        return {
            "case_id": case.case_id,
            "tags": case.tags,
            "status": "completed",
            **evaluation,
            "runtime_metrics": _runtime_metrics(
                started_at,
                usage_trace,
                agent_trace,
            ),
            "manual_review": {
                "interest_alignment": None,
                "route_coherence": None,
                "description_quality": None,
                "review_scale": "0-5",
            },
            "agent_trace": agent_trace,
            "agent_quality_trace": agent_quality_trace,
            "itinerary": itinerary,
        }
    except Exception as exc:
        return {
            "case_id": case.case_id,
            "tags": case.tags,
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "runtime_metrics": _runtime_metrics(
                started_at,
                usage_trace,
                agent_trace,
            ),
            "agent_trace": agent_trace,
            "agent_quality_trace": agent_quality_trace,
        }


# === Agent 在线评测入口：显式确认后运行四工具链与确定性评分器 ===
# 流程：固定案例 → DeepSeek Agent → 高德四工具 → 评分 → JSON 报告
def select_cases(
    cases: list,
    suite: str,
    case_id: str | None = None,
) -> list:
    if case_id:
        selected = [case for case in cases if case.case_id == case_id]
        if not selected:
            raise ValueError(f"Unknown evaluation case: {case_id}")
        return selected
    if suite == "smoke":
        return [case for case in cases if case.is_smoke_case]
    return cases


def main():
    arguments = _parse_arguments()
    if not arguments.live:
        raise SystemExit(
            "Live evaluation was not started. Add --live to allow "
            "DeepSeek and AMap API calls."
        )

    try:
        cases = select_cases(
            load_itinerary_cases(),
            arguments.suite,
            arguments.case_id,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    results = [run_evaluation_case(case) for case in cases]
    suite_name = (
        f"case:{arguments.case_id}"
        if arguments.case_id
        else arguments.suite
    )
    report_data = {
        "evaluation_version": "itinerary-stability-matrix-v1",
        "suite": suite_name,
        "model": DEEPSEEK_MODEL,
        "prompt_version": AGENT_PROMPT_VERSION,
        "summary": summarize_results(results),
        "results": results,
    }

    report = json.dumps(
        report_data,
        ensure_ascii=False,
        indent=2,
        default=str,
    )
    print(report)

    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(report + "\n", encoding="utf-8")

    if not report_data["summary"]["meets_stability_gate"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
