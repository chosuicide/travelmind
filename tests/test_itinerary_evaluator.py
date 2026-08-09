import unittest
from datetime import date
from types import SimpleNamespace

from app.generation.policy import PACE_ACTIVITY_RANGES
from evals.cases import load_itinerary_cases
from evals.itinerary_evaluator import evaluate_itinerary


# === 行程评测器测试：用固定数据验证分数和失败诊断，不调用外部 API ===
# 流程：Trip + 模拟行程 → 确定性检查 → 验证得分与具体失败项
class ItineraryEvaluatorTests(unittest.TestCase):
    def setUp(self):
        self.trip = SimpleNamespace(
            start_date=date(2026, 10, 1),
            end_date=date(2026, 10, 2),
            budget=500,
            pace="balanced",
        )

    def test_pace_ranges_match_the_generation_rules(self):
        self.assertEqual(
            PACE_ACTIVITY_RANGES,
            {
                "relaxed": (1, 3),
                "balanced": (2, 4),
                "intensive": (3, 5),
            },
        )

    @staticmethod
    def _activity(
        name: str,
        start_time: str,
        end_time: str,
        cost: float,
        verified: bool = True,
        latitude: float = 23.1291,
        longitude: float = 113.2644,
        amap_id: str | None = None,
    ) -> dict:
        return {
            "name": name,
            "location": "测试地址",
            "start_time": start_time,
            "end_time": end_time,
            "estimated_cost": cost,
            "description": "测试活动",
            "verified_place": (
                {
                    "amap_id": amap_id or f"poi-{name}",
                    "latitude": latitude,
                    "longitude": longitude,
                }
                if verified
                else None
            ),
        }

    def test_valid_itinerary_passes_all_deterministic_checks(self):
        itinerary = {
            "days": [
                {
                    "day_number": 1,
                    "activities": [
                        self._activity(
                            "地点一",
                            "09:00",
                            "11:00",
                            100,
                        ),
                        self._activity(
                            "地点二",
                            "13:00",
                            "15:00",
                            100,
                        ),
                    ],
                },
                {
                    "day_number": 2,
                    "activities": [
                        self._activity(
                            "地点三",
                            "10:00",
                            "12:00",
                            100,
                        ),
                        self._activity(
                            "地点四",
                            "14:00",
                            "16:00",
                            50,
                        ),
                    ],
                },
            ]
        }

        result = evaluate_itinerary(self.trip, itinerary)

        self.assertTrue(result["passed_all_checks"])
        self.assertEqual(result["score"], 100.0)
        self.assertEqual(
            result["metrics"]["total_activity_cost"],
            350.0,
        )
        self.assertTrue(
            result["quality_signals"]
            ["pace_density_matches"]
            ["passed"]
        )
        self.assertTrue(
            result["quality_signals"]
            ["reasonable_transfer_distances"]
            ["passed"]
        )
        self.assertTrue(
            result["quality_signals"]
            ["unique_verified_places"]
            ["passed"]
        )

    def test_reports_overlap_budget_and_verification_failures(self):
        itinerary = {
            "days": [
                {
                    "day_number": 1,
                    "activities": [
                        self._activity(
                            "地点一",
                            "09:00",
                            "12:00",
                            400,
                        ),
                        self._activity(
                            "地点二",
                            "11:00",
                            "13:00",
                            200,
                            verified=False,
                        ),
                    ],
                },
                {
                    "day_number": 2,
                    "activities": [
                        self._activity(
                            "地点三",
                            "10:00",
                            "12:00",
                            0,
                        ),
                    ],
                },
            ]
        }

        result = evaluate_itinerary(self.trip, itinerary)

        self.assertFalse(result["passed_all_checks"])
        self.assertFalse(
            result["checks"]["no_time_overlap"]["passed"]
        )
        self.assertFalse(
            result["checks"]
            ["activity_cost_within_trip_budget"]
            ["passed"]
        )
        self.assertFalse(
            result["checks"]["all_places_verified"]["passed"]
        )

    def test_long_transfer_is_a_signal_not_a_hard_score_failure(self):
        itinerary = {
            "days": [
                {
                    "day_number": 1,
                    "activities": [
                        self._activity(
                            "广州地点",
                            "09:00",
                            "11:00",
                            50,
                            latitude=23.1291,
                            longitude=113.2644,
                        ),
                        self._activity(
                            "深圳地点",
                            "13:00",
                            "15:00",
                            50,
                            latitude=22.5431,
                            longitude=114.0579,
                        ),
                    ],
                },
                {
                    "day_number": 2,
                    "activities": [
                        self._activity(
                            "广州地点二",
                            "10:00",
                            "12:00",
                            50,
                        ),
                    ],
                },
            ]
        }

        result = evaluate_itinerary(self.trip, itinerary)

        self.assertEqual(result["score"], 100.0)
        self.assertFalse(
            result["quality_signals"]
            ["reasonable_transfer_distances"]
            ["passed"]
        )
        self.assertGreater(
            result["metrics"]["max_transfer_straight_line_km"],
            25,
        )

    def test_duplicate_amap_id_is_reported_as_quality_signal(self):
        itinerary = {
            "days": [
                {
                    "day_number": 1,
                    "activities": [
                        self._activity(
                            "广州圣心大教堂",
                            "09:00",
                            "11:00",
                            0,
                            amap_id="same-poi",
                        ),
                        self._activity(
                            "其他地点",
                            "13:00",
                            "15:00",
                            0,
                        ),
                    ],
                },
                {
                    "day_number": 2,
                    "activities": [
                        self._activity(
                            "石室圣心大教堂",
                            "10:00",
                            "12:00",
                            0,
                            amap_id="same-poi",
                        ),
                        self._activity(
                            "另一个地点",
                            "14:00",
                            "16:00",
                            0,
                        ),
                    ],
                },
            ]
        }

        result = evaluate_itinerary(self.trip, itinerary)

        self.assertEqual(result["score"], 100.0)
        self.assertFalse(
            result["quality_signals"]
            ["unique_verified_places"]
            ["passed"]
        )
        duplicates = result["metrics"]["duplicate_verified_places"]
        self.assertEqual(len(duplicates), 1)
        self.assertEqual(duplicates[0]["amap_id"], "same-poi")

    def test_all_fixed_cases_follow_real_trip_input_rules(self):
        cases = load_itinerary_cases()

        self.assertEqual(len(cases), 8)
        self.assertEqual(
            len({case.case_id for case in cases}),
            len(cases),
        )


if __name__ == "__main__":
    unittest.main()
