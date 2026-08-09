import unittest
from types import SimpleNamespace

from app.agent.quality import assess_itinerary_quality


def _activity(
    name: str,
    district: str,
    latitude: float,
    longitude: float,
    selection_role: str = "primary",
) -> dict:
    return {
        "name": name,
        "verified_place": {
            "district": district,
            "latitude": latitude,
            "longitude": longitude,
            "selection_role": selection_role,
        },
    }


# === Agent 质量门测试：把可计算的问题转换成明确的 AI 修订意见 ===
# 流程：已验证行程 → 密度/可用性/折返/距离检查 → 稳定问题列表
class AgentQualityTests(unittest.TestCase):
    def test_reports_density_auxiliary_place_and_backtracking(self):
        trip = SimpleNamespace(pace="balanced")
        itinerary = {
            "days": [
                {
                    "day_number": 1,
                    "activities": [
                        _activity("荔湾景点1", "荔湾区", 23.11, 113.24),
                        _activity("荔湾景点2", "荔湾区", 23.12, 113.24),
                        _activity(
                            "赤岗塔(不对外开放)",
                            "海珠区",
                            23.10,
                            113.32,
                        ),
                        _activity("荔湾景点3", "荔湾区", 23.11, 113.24),
                        _activity("荔湾景点4", "荔湾区", 23.12, 113.24),
                        _activity("荔湾景点5", "荔湾区", 23.13, 113.24),
                    ],
                }
            ]
        }

        issues = assess_itinerary_quality(trip, itinerary)

        self.assertTrue(any("has 6 activities" in issue for issue in issues))
        self.assertTrue(any("不对外开放" in issue for issue in issues))
        self.assertTrue(any("backtracks" in issue for issue in issues))
        self.assertTrue(any("straight-line" in issue for issue in issues))

    def test_accepts_compact_same_district_day(self):
        trip = SimpleNamespace(pace="balanced")
        itinerary = {
            "days": [
                {
                    "day_number": 1,
                    "activities": [
                        _activity("陈家祠", "荔湾区", 23.12, 113.24),
                        _activity("荔枝湾", "荔湾区", 23.11, 113.23),
                    ],
                }
            ]
        }

        self.assertEqual(assess_itinerary_quality(trip, itinerary), [])

    def test_balanced_pace_rejects_five_activities(self):
        trip = SimpleNamespace(pace="balanced")
        itinerary = {
            "days": [
                {
                    "day_number": 1,
                    "activities": [
                        _activity(
                            f"景点{index}",
                            "荔湾区",
                            23.12,
                            113.24,
                        )
                        for index in range(5)
                    ],
                }
            ]
        }

        issues = assess_itinerary_quality(trip, itinerary)

        self.assertTrue(
            any("balanced pace requires 2 to 4" in issue for issue in issues)
        )

    def test_reports_sub_poi_from_amap_parent_relationship(self):
        trip = SimpleNamespace(pace="relaxed")
        itinerary = {
            "days": [
                {
                    "day_number": 1,
                    "activities": [
                        _activity(
                            "神楼",
                            "荔湾区",
                            23.1271,
                            113.2454,
                            selection_role="sub_poi",
                        )
                    ],
                }
            ]
        }

        issues = assess_itinerary_quality(trip, itinerary)

        self.assertEqual(len(issues), 1)
        self.assertIn("selects sub-POI", issues[0])

    def test_real_route_can_clear_straight_line_warning(self):
        first = _activity("黄埔古港", "海珠区", 23.10, 113.50)
        first.update(
            {
                "place_provider_id": "origin",
                "start_time": "09:00",
                "end_time": "12:00",
            }
        )
        second = _activity("广州塔", "海珠区", 23.10, 113.32)
        second.update(
            {
                "place_provider_id": "destination",
                "start_time": "13:00",
                "end_time": "16:00",
            }
        )
        trip = SimpleNamespace(
            pace="balanced",
            checked_routes={
                ("origin", "destination"): {
                    "duration_minutes": 35.0,
                }
            },
        )
        itinerary = {
            "days": [
                {
                    "day_number": 1,
                    "activities": [first, second],
                }
            ]
        }

        self.assertEqual(assess_itinerary_quality(trip, itinerary), [])

    def test_reports_english_summary_and_description(self):
        activity = _activity("岳阳楼景区", "岳阳楼区", 29.38, 113.09)
        activity["description"] = (
            "Historic landmark tower beside the beautiful Dongting Lake."
        )
        trip = SimpleNamespace(pace="relaxed")
        itinerary = {
            "days": [
                {
                    "day_number": 1,
                    "summary": (
                        "Day one explores the iconic Yueyang Tower landmark"
                    ),
                    "activities": [activity],
                }
            ]
        }

        issues = assess_itinerary_quality(trip, itinerary)

        self.assertTrue(any("summary must be written" in item for item in issues))
        self.assertTrue(any("description must be written" in item for item in issues))

    def test_allows_chinese_copy_with_official_latin_brand_names(self):
        activity = _activity("长沙IFS", "芙蓉区", 28.19, 112.98)
        activity["description"] = "逛长沙IFS与附近街区，感受城市商业氛围。"
        trip = SimpleNamespace(pace="relaxed")
        itinerary = {
            "days": [
                {
                    "day_number": 1,
                    "summary": "长沙IFS与太平街",
                    "activities": [activity],
                }
            ]
        }

        self.assertEqual(assess_itinerary_quality(trip, itinerary), [])


if __name__ == "__main__":
    unittest.main()
