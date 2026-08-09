import json
import unittest
from unittest.mock import Mock, patch

from pydantic import ValidationError

from app.agent.context import PlanningContext
from app.agent.tools import TRAVEL_TOOLS, execute_travel_tool
from app.integrations.amap import estimate_place_route, fetch_place_detail


def _place(place_id: str, name: str, longitude: float) -> dict:
    return {
        "amap_id": place_id,
        "name": name,
        "address": "测试地址",
        "city": "广州市",
        "district": "荔湾区",
        "latitude": 23.12,
        "longitude": longitude,
        "citycode": "020",
        "adcode": "440103",
        "type": "风景名胜;旅游景点",
        "typecode": "110200",
        "parent_id": "",
        "selection_role": "primary",
        "match_score": 200.0,
        "business": {},
    }


def _draft(place_id: str) -> dict:
    return {
        "days": [
            {
                "day_number": 1,
                "summary": "测试行程",
                "activities": [
                    {
                        "place_provider_id": place_id,
                        "name": "AI 名称",
                        "location": "AI 地址",
                        "start_time": "09:00",
                        "end_time": "11:00",
                        "estimated_cost": 20,
                        "description": "测试",
                    }
                ],
            }
        ]
    }


# === Agent 四工具测试：搜索引入 ID，其余工具只能消费已见可信地点 ===
# 流程：工具 Schema → 上下文归属 → 外部能力 Mock → 统一观察结果
class AgentToolTests(unittest.TestCase):
    def setUp(self):
        self.context = PlanningContext(
            destination="广州",
            pace="relaxed",
        )
        self.origin = _place("poi-1", "陈家祠堂", 113.245)
        self.destination = _place("poi-2", "荔枝湾", 113.236)
        self.context.remember_places([self.origin, self.destination])

    def test_exactly_four_tools_are_exposed(self):
        names = {
            tool["function"]["name"]
            for tool in TRAVEL_TOOLS
        }
        self.assertEqual(
            names,
            {
                "search_places",
                "get_place_details",
                "estimate_route",
                "check_itinerary",
            },
        )

    def test_one_day_detail_tool_is_limited_to_two_calls(self):
        context = PlanningContext(
            destination="广州",
            total_days=1,
            max_tool_calls=10,
        )
        context.reserve_tool_call("get_place_details")
        context.reserve_tool_call("get_place_details")

        with self.assertRaisesRegex(ValueError, "2-call limit"):
            context.reserve_tool_call("get_place_details")

    def test_budget_snapshot_decrements_and_caps_each_tool(self):
        context = PlanningContext(
            destination="广州",
            total_days=1,
            max_tool_calls=3,
        )

        initial = context.tool_budget_snapshot()
        self.assertEqual(initial["remaining_total"], 3)
        self.assertEqual(initial["remaining_by_tool"]["search_places"], 3)
        self.assertEqual(
            initial["remaining_by_tool"]["get_place_details"],
            2,
        )

        context.reserve_tool_call("get_place_details")
        context.reserve_tool_call("get_place_details")
        exhausted = context.tool_budget_snapshot()

        self.assertEqual(exhausted["remaining_total"], 1)
        self.assertEqual(
            exhausted["remaining_by_tool"]["get_place_details"],
            0,
        )
        self.assertEqual(exhausted["remaining_by_tool"]["search_places"], 1)

    @patch("app.agent.tools.fetch_place_detail")
    def test_get_details_requires_seen_id_and_updates_business(
        self,
        mock_fetch,
    ):
        mock_fetch.return_value = {
            **self.origin,
            "business": {
                "opentime_today": "09:00-18:00",
                "rating": "4.9",
            },
        }

        result = execute_travel_tool(
            "get_place_details",
            json.dumps({"place_provider_id": "poi-1"}),
            self.context,
        )

        self.assertEqual(result["content"]["business"]["rating"], "4.9")
        self.assertEqual(result["places"][0]["amap_id"], "poi-1")

        with self.assertRaisesRegex(ValueError, "not returned"):
            execute_travel_tool(
                "get_place_details",
                json.dumps({"place_provider_id": "unknown"}),
                self.context,
            )
        self.assertEqual(mock_fetch.call_count, 1)

    @patch("app.agent.tools.estimate_place_route")
    def test_estimate_route_uses_seen_places_only(self, mock_estimate):
        mock_estimate.return_value = {
            "mode": "walking",
            "distance_meters": 2000,
            "duration_minutes": 25.0,
        }
        arguments = {
            "origin_place_id": "poi-1",
            "destination_place_id": "poi-2",
            "mode": "walking",
        }

        result = execute_travel_tool(
            "estimate_route",
            json.dumps(arguments),
            self.context,
        )

        self.assertEqual(result["content"]["distance_meters"], 2000)
        self.assertEqual(
            self.context.routes_by_pair[("poi-1", "poi-2")][
                "duration_minutes"
            ],
            25.0,
        )
        mock_estimate.assert_called_once_with(
            self.origin,
            self.destination,
            "walking",
        )

        arguments["destination_place_id"] = "unknown"
        with self.assertRaisesRegex(ValueError, "not returned"):
            execute_travel_tool(
                "estimate_route",
                json.dumps(arguments),
                self.context,
            )

    def test_check_itinerary_binds_seen_places_before_checking(self):
        result = execute_travel_tool(
            "check_itinerary",
            json.dumps({"draft": _draft("poi-1")}),
            self.context,
        )
        self.assertTrue(result["content"]["valid"])
        self.assertEqual(result["content"]["issues"], [])
        self.assertIsNotNone(result["terminal_itinerary"])

        with self.assertRaisesRegex(ValueError, "not returned"):
            execute_travel_tool(
                "check_itinerary",
                json.dumps({"draft": _draft("unknown")}),
                self.context,
            )

    def test_check_itinerary_rejects_wrong_day_count(self):
        self.context.total_days = 2

        result = execute_travel_tool(
            "check_itinerary",
            json.dumps({"draft": _draft("poi-1")}),
            self.context,
        )

        self.assertFalse(result["content"]["valid"])
        self.assertIn(
            "incorrect number of days",
            result["content"]["issues"][0],
        )
        self.assertIsNone(result["terminal_itinerary"])

    def test_check_itinerary_detects_overlap_and_budget_excess(self):
        self.context.budget = 30
        draft = _draft("poi-1")
        draft["days"][0]["activities"].append(
            {
                "place_provider_id": "poi-2",
                "name": "AI 名称二",
                "location": "AI 地址二",
                "start_time": "10:30",
                "end_time": "12:00",
                "estimated_cost": 20,
                "description": "测试二",
            }
        )

        result = execute_travel_tool(
            "check_itinerary",
            json.dumps({"draft": draft}),
            self.context,
        )

        issues = " ".join(result["content"]["issues"])
        self.assertIn("overlapping activities", issues)
        self.assertIn("exceeds the trip budget", issues)
        self.assertIsNone(result["terminal_itinerary"])

    def test_tool_arguments_reject_extra_fields(self):
        with self.assertRaises(ValidationError):
            execute_travel_tool(
                "get_place_details",
                json.dumps(
                    {
                        "place_provider_id": "poi-1",
                        "database_query": "DROP TABLE trips",
                    }
                ),
                self.context,
            )


class AMapToolIntegrationShapeTests(unittest.TestCase):
    @patch("app.integrations.amap.httpx.get")
    def test_fetch_detail_requests_business_fields(self, mock_get):
        response = Mock()
        response.json.return_value = {
            "status": "1",
            "pois": [
                {
                    "id": "poi-1",
                    "name": "陈家祠堂",
                    "parent": "",
                    "address": "中山七路",
                    "cityname": "广州市",
                    "adname": "荔湾区",
                    "citycode": "020",
                    "adcode": "440103",
                    "location": "113.245,23.12",
                    "type": "风景名胜",
                    "typecode": "110202",
                    "business": {
                        "opentime_today": "09:00-18:00",
                        "rating": "4.9",
                    },
                }
            ],
        }
        mock_get.return_value = response

        detail = fetch_place_detail("poi-1")

        self.assertEqual(detail["business"]["rating"], "4.9")
        params = mock_get.call_args.kwargs["params"]
        self.assertEqual(params["show_fields"], "business")

    @patch("app.integrations.amap.httpx.get")
    def test_route_parser_returns_compact_summary(self, mock_get):
        response = Mock()
        response.json.return_value = {
            "status": "1",
            "route": {
                "paths": [
                    {
                        "distance": "2090",
                        "cost": {"duration": "1672"},
                        "steps": [
                            {
                                "polyline": (
                                    "113.245000,23.120000;"
                                    "113.240000,23.121000"
                                )
                            },
                            {
                                "polyline": (
                                    "113.240000,23.121000;"
                                    "113.236000,23.122000"
                                )
                            },
                        ],
                    }
                ]
            },
        }
        mock_get.return_value = response

        result = estimate_place_route(
            _place("poi-1", "陈家祠堂", 113.245),
            _place("poi-2", "荔枝湾", 113.236),
            "walking",
        )

        self.assertEqual(result["distance_meters"], 2090)
        self.assertEqual(result["duration_minutes"], 27.9)
        self.assertEqual(result["estimated_cost"], 0.0)
        self.assertEqual(
            result["polyline"],
            [
                [113.245, 23.12],
                [113.24, 23.121],
                [113.236, 23.122],
            ],
        )
        self.assertEqual(
            mock_get.call_args.kwargs["params"]["show_fields"],
            "cost,polyline",
        )


if __name__ == "__main__":
    unittest.main()
