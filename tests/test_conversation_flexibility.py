import unittest
from datetime import date

from app.conversations.policy import assistant_reply
from app.conversations.schemas import TripDraft
from app.conversations.state import (
    build_trip_input,
    complete_preview_defaults,
    get_missing_fields,
)


# === 模块：对话自由度回归测试 ===
# 流程：只提供方向 → 生成标记默认值 → 用户预览确认 → 构建合法 Trip
class ConversationFlexibilityTests(unittest.TestCase):
    def setUp(self):
        self.core_draft = {
            "province_code": "450000",
            "province_name": "广西壮族自治区",
            "city_code": "450300",
            "city_name": "桂林市",
            "start_date": "2026-08-20",
            "end_date": "2026-08-23",
            "budget": 3000,
            "people": 2,
        }

    def test_optional_preferences_do_not_block_confirmation(self):
        self.assertEqual(get_missing_fields(self.core_draft), [])

    def test_trip_build_uses_neutral_defaults(self):
        trip_input = build_trip_input(self.core_draft)
        self.assertEqual(trip_input.interests, ["城市漫游", "本地体验"])
        self.assertEqual(trip_input.pace, "balanced")
        self.assertEqual(trip_input.start_date, date(2026, 8, 20))

    def test_flexible_budget_is_ready_and_gets_internal_planning_ceiling(self):
        draft = {**self.core_draft, "budget": None, "budget_flexible": True}
        self.assertEqual(get_missing_fields(draft), [])
        trip_input = build_trip_input(draft)
        self.assertEqual(trip_input.budget, 16000)
        self.assertIn("预算可灵活安排", trip_input.notes)

    def test_ai_acknowledgement_cannot_override_grounded_reply(self):
        reply = assistant_reply(
            self.core_draft,
            changed=True,
            acknowledgement="你将和 9 个人旅行到错误城市。",
        )
        self.assertNotIn("9 个人", reply)
        self.assertNotIn("错误城市", reply)
        self.assertIn("关键信息已经齐了", reply)

    def test_ai_cannot_repeat_a_question_for_saved_dates(self):
        reply = assistant_reply(
            self.core_draft,
            changed=True,
            acknowledgement="我再确认一下，你计划哪天出发、哪天结束？",
        )

        self.assertNotIn("哪天出发", reply)
        self.assertNotIn("哪天结束", reply)
        self.assertIn("关键信息已经齐了", reply)

    def test_preview_can_mark_delegated_people_and_budget_defaults(self):
        incomplete = dict(self.core_draft)
        incomplete.pop("people")
        incomplete.pop("budget")

        candidate, assumed = complete_preview_defaults(
            TripDraft.model_validate(incomplete)
        )

        self.assertEqual(get_missing_fields(candidate), [])
        self.assertEqual(candidate.people, 1)
        self.assertTrue(candidate.budget_flexible)
        self.assertIn("people", assumed)
        self.assertIn("budget", assumed)

    def test_preview_can_default_dates_only_after_preview_is_requested(self):
        incomplete = TripDraft.model_validate(
            {
                "province_code": "330000",
                "province_name": "浙江省",
                "city_code": "330500",
                "city_name": "湖州市",
            }
        )

        candidate, assumed = complete_preview_defaults(
            incomplete,
            reference_day=date(2026, 8, 9),
        )

        self.assertEqual(candidate.start_date, date(2026, 8, 15))
        self.assertEqual(candidate.end_date, date(2026, 8, 16))
        self.assertIn("start_date", assumed)
        self.assertIn("end_date", assumed)


if __name__ == "__main__":
    unittest.main()
