import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

from app.agent.prompts import build_agent_messages
from app.generation.policy import (
    MAX_TRIP_DAYS,
    PACE_ACTIVITY_RANGES,
    TOOL_CALL_LIMITS_BY_MAX_DAYS,
    get_tool_call_limits,
)
from app.generation.service import GenerationTelemetry
from app.integrations.deepseek import generate_itinerary_with_tools
from app.trips.schemas import TripCreate


def _trip(total_days: int):
    start_date = date(2026, 10, 1)
    return SimpleNamespace(
        destination="广州",
        start_date=start_date,
        end_date=start_date + timedelta(days=total_days - 1),
        budget=5000,
        people=2,
        interests=["历史文化"],
        pace="balanced",
        notes=None,
    )


# === 生成规模限制测试：天数、工具预算和提示词必须使用同一套规则 ===
# 流程：1/3/5/6 天输入 → Pydantic边界 → Agent预算 → Prompt规则
class GenerationLimitTests(unittest.TestCase):
    def test_generation_policy_is_the_single_rule_source(self):
        self.assertEqual(MAX_TRIP_DAYS, 5)
        self.assertEqual(
            max(TOOL_CALL_LIMITS_BY_MAX_DAYS),
            MAX_TRIP_DAYS,
        )
        self.assertEqual(
            PACE_ACTIVITY_RANGES,
            {
                "relaxed": (1, 3),
                "balanced": (2, 4),
                "intensive": (3, 5),
            },
        )

    def test_trip_create_allows_five_days_and_rejects_six(self):
        five_days = _trip(5)
        validated = TripCreate.model_validate(vars(five_days))
        self.assertEqual(
            (validated.end_date - validated.start_date).days + 1,
            5,
        )

        with self.assertRaisesRegex(
            ValidationError,
            "trip cannot be longer than 5 days",
        ):
            TripCreate.model_validate(vars(_trip(6)))

    def test_tool_limits_are_tiered_by_trip_length(self):
        self.assertEqual(
            get_tool_call_limits(1),
            {
                "search_places": 4,
                "get_place_details": 2,
                "estimate_route": 2,
                "check_itinerary": 2,
            },
        )
        self.assertEqual(sum(get_tool_call_limits(3).values()), 17)
        self.assertEqual(sum(get_tool_call_limits(5).values()), 22)

    @patch("app.agent.PlanningAgent")
    def test_real_agent_entry_uses_tiered_total_budget(
        self,
        mock_agent_class,
    ):
        mock_agent_class.return_value.run.return_value = {"days": []}

        for total_days, expected_budget in ((1, 10), (3, 17), (5, 22)):
            with self.subTest(total_days=total_days):
                generate_itinerary_with_tools(_trip(total_days))
                self.assertEqual(
                    mock_agent_class.call_args.kwargs["max_tool_calls"],
                    expected_budget,
                )
                self.assertEqual(
                    mock_agent_class.call_args.kwargs["max_model_turns"],
                    expected_budget + 4,
                )
                self.assertIsNone(
                    mock_agent_class.call_args.kwargs["thread_id"]
                )

    @patch("app.agent.PlanningAgent")
    def test_real_agent_entry_rejects_six_days_before_model_call(
        self,
        mock_agent_class,
    ):
        with self.assertRaisesRegex(ValueError, "between 1 and 5 days"):
            generate_itinerary_with_tools(_trip(6))
        mock_agent_class.assert_not_called()

    def test_agent_prompt_uses_new_density_and_three_day_budget(self):
        prompt = build_agent_messages(
            _trip(3),
            max_tool_calls=17,
        )[0]["content"]

        self.assertIn("at most 17 tool calls", prompt)
        self.assertIn("Search at most\n  8 times", prompt)
        self.assertIn("Every tool result includes tool_budget", prompt)
        self.assertIn("remaining_by_tool", prompt)
        self.assertIn("count the whole batch first", prompt)
        self.assertIn("balanced means 2 to 4", prompt)
        self.assertIn("intensive means 3 to 5", prompt)
        self.assertIn("natural Simplified Chinese", prompt)
        self.assertIn("岳阳楼与洞庭湖", prompt)

    def test_generation_trace_keeps_sanitized_budget_snapshot(self):
        telemetry = GenerationTelemetry()
        telemetry.record_tool(
            {
                "tool_name": "search_places",
                "arguments": {},
                "status": "succeeded",
                "tool_budget": {
                    "remaining_total": 16,
                    "remaining_by_tool": {
                        "search_places": 7,
                        "get_place_details": 3,
                        "estimate_route": 4,
                        "check_itinerary": 2,
                    },
                },
            }
        )

        self.assertEqual(
            telemetry.trace[0]["tool_budget"]["remaining_total"],
            16,
        )
        self.assertEqual(
            telemetry.trace[0]["tool_budget"]["remaining_by_tool"]
            ["search_places"],
            7,
        )

    def test_generation_trace_records_langgraph_node_events(self):
        telemetry = GenerationTelemetry()

        telemetry.record_graph(
            {
                "node": "model",
                "status": "completed",
                "turn": 2,
                "next": "tools",
            }
        )

        self.assertEqual(
            telemetry.trace,
            [
                {
                    "type": "graph",
                    "node": "model",
                    "status": "completed",
                    "turn": 2,
                    "next": "tools",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
