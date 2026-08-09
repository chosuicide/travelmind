import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.integrations.amap import (
    bind_itinerary_candidate_places,
    discover_attraction_candidates,
)
from app.prompt_engine import PromptBuilder


def _candidate(
    poi_id: str,
    name: str,
    address: str = "测试路1号",
) -> dict:
    return {
        "amap_id": poi_id,
        "name": name,
        "address": address,
        "city": "广州市",
        "district": "越秀区",
        "latitude": 23.1291,
        "longitude": 113.2644,
        "type": "风景名胜;旅游景点",
        "typecode": "110000",
    }


# === 候选池实验测试：真实 POI 先于 AI，AI 只选择后端允许的 ID ===
# 流程：高德候选 → Prompt 白名单 → AI ID → 后端归属/去重/标准数据绑定
class CandidatePoolTests(unittest.TestCase):
    def setUp(self):
        self.trip = SimpleNamespace(
            destination="广州",
            start_date=date(2026, 10, 1),
            end_date=date(2026, 10, 2),
            budget=3000,
            people=2,
            interests=["历史文化"],
            pace="balanced",
            notes=None,
        )
        self.candidates = [
            _candidate("poi-1", "越秀公园"),
            _candidate("poi-2", "中山纪念堂"),
        ]

    def test_prompt_contains_closed_candidate_ids_and_rules(self):
        messages = PromptBuilder(
            self.trip,
            place_candidates=self.candidates,
        ).build()

        self.assertIn(
            "Every activity must select exactly one POI",
            messages[0]["content"],
        )
        self.assertIn("place_provider_id", messages[0]["content"])
        self.assertIn("poi-1", messages[-1]["content"])
        self.assertIn("越秀公园", messages[-1]["content"])

    def test_binding_uses_canonical_candidate_data(self):
        itinerary = {
            "days": [
                {
                    "day_number": 1,
                    "activities": [
                        {
                            "place_provider_id": "poi-1",
                            "name": "AI invented alias",
                            "location": "AI invented address",
                        }
                    ],
                }
            ]
        }

        result = bind_itinerary_candidate_places(
            itinerary,
            self.candidates,
        )
        activity = result["days"][0]["activities"][0]

        self.assertEqual(activity["name"], "越秀公园")
        self.assertEqual(activity["location"], "测试路1号")
        self.assertEqual(
            activity["verified_place"]["amap_id"],
            "poi-1",
        )

    def test_unknown_or_duplicate_candidate_id_is_rejected(self):
        unknown_itinerary = {
            "days": [
                {
                    "activities": [
                        {"place_provider_id": "unknown"}
                    ]
                }
            ]
        }
        with self.assertRaisesRegex(ValueError, "unknown candidate"):
            bind_itinerary_candidate_places(
                unknown_itinerary,
                self.candidates,
            )

        duplicate_itinerary = {
            "days": [
                {
                    "activities": [
                        {"place_provider_id": "poi-1"},
                        {"place_provider_id": "poi-1"},
                    ]
                }
            ]
        }
        with self.assertRaisesRegex(ValueError, "duplicate candidate"):
            bind_itinerary_candidate_places(
                duplicate_itinerary,
                self.candidates,
            )

    @patch("app.integrations.amap.httpx.get")
    def test_discovery_uses_popular_and_interest_queries_and_deduplicates(
        self,
        mock_get,
    ):
        response = Mock()
        response.json.return_value = {
            "status": "1",
            "pois": [
                {
                    "id": "poi-1",
                    "name": "越秀公园",
                    "address": "解放北路988号",
                    "cityname": "广州市",
                    "adname": "越秀区",
                    "location": "113.265561,23.140096",
                    "type": "风景名胜;公园广场;公园",
                    "typecode": "110101",
                },
                {
                    "id": "poi-1",
                    "name": "重复结果",
                    "location": "113.265561,23.140096",
                },
            ],
        }
        mock_get.return_value = response

        candidates = discover_attraction_candidates(
            "广州",
            interests=["历史文化"],
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["amap_id"], "poi-1")
        self.assertEqual(mock_get.call_count, 2)
        first_params = mock_get.call_args_list[0].kwargs["params"]
        second_params = mock_get.call_args_list[1].kwargs["params"]
        self.assertEqual(first_params["keywords"], "广州著名景点")
        self.assertEqual(first_params["page_size"], 15)
        self.assertEqual(
            second_params["keywords"],
            "广州历史文化景点",
        )
        self.assertEqual(second_params["page_size"], 10)


if __name__ == "__main__":
    unittest.main()
