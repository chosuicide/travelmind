import unittest

from evals.cases import load_itinerary_cases
from evals.reporting import summarize_results
from evals.run_itinerary_eval import run_evaluation_case, select_cases


def _completed_result(case_id: str = "case-pass") -> dict:
    return {
        "case_id": case_id,
        "status": "completed",
        "passed_all_checks": True,
        "quality_signals": {
            "pace_density_matches": {"passed": True},
            "reasonable_transfer_distances": {"passed": True},
            "unique_verified_places": {"passed": True},
        },
        "agent_trace": [
            {"tool_name": "search_places", "status": "succeeded"}
        ],
        "runtime_metrics": {
            "duration_seconds": 12.5,
            "input_tokens": 100,
            "output_tokens": 40,
        },
    }


# === 稳定性矩阵测试：验证案例覆盖、失败隔离和汇总门槛 ===
# 流程：固定案例/模拟结果 → 矩阵汇总 → 指标与稳定性结论
class EvaluationMatrixTests(unittest.TestCase):
    def test_matrix_covers_product_boundaries(self):
        cases = load_itinerary_cases()
        trip_days = {
            (case.end_date - case.start_date).days + 1
            for case in cases
        }

        self.assertEqual(len(cases), 8)
        self.assertEqual(trip_days, {1, 2, 3, 4, 5})
        self.assertEqual(
            {case.pace for case in cases},
            {"relaxed", "balanced", "intensive"},
        )
        self.assertEqual(sum(case.is_smoke_case for case in cases), 1)
        self.assertTrue(any(case.budget <= 1200 for case in cases))
        self.assertTrue(any(case.budget >= 9000 for case in cases))
        self.assertNotIn("tags", cases[0].trip_data())
        self.assertEqual(len(select_cases(cases, "smoke")), 1)
        self.assertEqual(len(select_cases(cases, "matrix")), 8)
        with self.assertRaisesRegex(ValueError, "Unknown evaluation case"):
            select_cases(cases, "matrix", "missing-case")

    def test_summary_passes_when_every_case_and_signal_passes(self):
        summary = summarize_results(
            [_completed_result("case-a"), _completed_result("case-b")]
        )

        self.assertTrue(summary["meets_stability_gate"])
        self.assertEqual(summary["completion_rate"], 1.0)
        self.assertEqual(summary["case_pass_rate"], 1.0)
        self.assertEqual(summary["total_tokens"], 280)
        self.assertEqual(summary["tool_status_counts"]["succeeded"], 2)

    def test_summary_fails_gate_but_keeps_all_case_results(self):
        failed = {
            "case_id": "case-failed",
            "status": "failed",
            "agent_trace": [
                {"tool_name": "search_places", "status": "rejected"}
            ],
            "runtime_metrics": {"duration_seconds": 3},
        }
        summary = summarize_results([_completed_result(), failed])

        self.assertFalse(summary["meets_stability_gate"])
        self.assertEqual(summary["total_cases"], 2)
        self.assertEqual(summary["completed_cases"], 1)
        self.assertEqual(summary["failed_case_ids"], ["case-failed"])
        self.assertEqual(summary["tool_status_counts"]["rejected"], 1)

    def test_single_case_failure_is_converted_to_a_result(self):
        case = load_itinerary_cases()[0]

        def failing_generator(
            trip,
            on_tool_result,
            on_quality_result,
            on_model_usage,
        ):
            on_model_usage(
                {
                    "input_tokens": 25,
                    "output_tokens": 5,
                    "total_tokens": 30,
                }
            )
            on_tool_result(
                {
                    "tool_name": "search_places",
                    "status": "failed",
                }
            )
            raise RuntimeError("simulated upstream failure")

        result = run_evaluation_case(case, generator=failing_generator)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_type"], "RuntimeError")
        self.assertIn("simulated upstream", result["error"])
        self.assertEqual(result["runtime_metrics"]["model_turns"], 1)
        self.assertEqual(result["runtime_metrics"]["input_tokens"], 25)
        self.assertEqual(result["runtime_metrics"]["tool_attempts"], 1)


if __name__ == "__main__":
    unittest.main()
