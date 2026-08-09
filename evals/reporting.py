STABILITY_THRESHOLDS = {
    "minimum_completion_rate": 0.8,
    "minimum_case_pass_rate": 0.8,
    "maximum_rejected_tool_rate": 0.2,
}


def _rate(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)


def _case_passed(result: dict) -> bool:
    quality_signals = result.get("quality_signals") or {}
    return (
        result.get("status") == "completed"
        and result.get("passed_all_checks") is True
        and bool(quality_signals)
        and all(
            signal.get("passed") is True
            for signal in quality_signals.values()
        )
    )


# === 评测矩阵汇总：把单案例结果转换成可比较的稳定性指标 ===
# 流程：案例结果 → 完成率/质量/成本指标 → 阈值检查 → 稳定性结论
def summarize_results(results: list[dict]) -> dict:
    total_cases = len(results)
    completed_results = [
        result
        for result in results
        if result.get("status") == "completed"
    ]
    passed_results = [
        result for result in results if _case_passed(result)
    ]
    hard_check_passes = sum(
        result.get("passed_all_checks") is True
        for result in completed_results
    )

    tool_events = [
        event
        for result in results
        for event in result.get("agent_trace", [])
    ]
    tool_status_counts = {
        status: sum(
            event.get("status") == status
            for event in tool_events
        )
        for status in ("succeeded", "rejected", "failed")
    }
    completion_rate = _rate(len(completed_results), total_cases)
    case_pass_rate = _rate(len(passed_results), total_cases)
    rejected_tool_rate = _rate(
        tool_status_counts["rejected"],
        len(tool_events),
    )

    threshold_checks = {
        "completion_rate": {
            "passed": (
                completion_rate
                >= STABILITY_THRESHOLDS["minimum_completion_rate"]
            ),
            "actual": completion_rate,
            "required": STABILITY_THRESHOLDS[
                "minimum_completion_rate"
            ],
        },
        "case_pass_rate": {
            "passed": (
                case_pass_rate
                >= STABILITY_THRESHOLDS["minimum_case_pass_rate"]
            ),
            "actual": case_pass_rate,
            "required": STABILITY_THRESHOLDS[
                "minimum_case_pass_rate"
            ],
        },
        "rejected_tool_rate": {
            "passed": (
                rejected_tool_rate
                <= STABILITY_THRESHOLDS["maximum_rejected_tool_rate"]
            ),
            "actual": rejected_tool_rate,
            "required_maximum": STABILITY_THRESHOLDS[
                "maximum_rejected_tool_rate"
            ],
        },
    }

    runtime_metrics = [
        result.get("runtime_metrics") or {}
        for result in results
    ]
    total_duration = round(
        sum(
            float(metrics.get("duration_seconds", 0))
            for metrics in runtime_metrics
        ),
        2,
    )
    total_input_tokens = sum(
        int(metrics.get("input_tokens", 0))
        for metrics in runtime_metrics
    )
    total_output_tokens = sum(
        int(metrics.get("output_tokens", 0))
        for metrics in runtime_metrics
    )

    return {
        "total_cases": total_cases,
        "completed_cases": len(completed_results),
        "passed_cases": len(passed_results),
        "hard_check_passes": hard_check_passes,
        "completion_rate": completion_rate,
        "case_pass_rate": case_pass_rate,
        "passed_case_ids": [
            result["case_id"] for result in passed_results
        ],
        "failed_case_ids": [
            result.get("case_id", "unknown")
            for result in results
            if not _case_passed(result)
        ],
        "tool_attempts": len(tool_events),
        "tool_status_counts": tool_status_counts,
        "rejected_tool_rate": rejected_tool_rate,
        "total_duration_seconds": total_duration,
        "average_duration_seconds": (
            round(total_duration / total_cases, 2)
            if total_cases
            else 0.0
        ),
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_tokens": total_input_tokens + total_output_tokens,
        "threshold_checks": threshold_checks,
        "meets_stability_gate": all(
            check["passed"] for check in threshold_checks.values()
        ),
    }
