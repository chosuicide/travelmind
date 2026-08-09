import json
import unittest
from datetime import date

from app.conversations.extractor import parse_extracted_message
from app.conversations.normalizer import (
    detect_context_patch,
    detect_interest_additions,
    normalize_patch,
    wants_generation,
)
from app.conversations.policy import next_question
from app.conversations.schemas import TripDraftPatch
from app.conversations.state import merge_draft
from evals.conversation_evaluator import evaluate_conversations


# === Conversation Engine v2：覆盖归一化、上下文覆盖、清空与偏好增删 ===
# 流程：简称补丁 → 官方地区 → 多轮修改 → 确定性追问 → 固定评测集
class ConversationEngineTests(unittest.TestCase):
    def test_missing_model_intent_is_recovered_conservatively(self):
        update = parse_extracted_message(
            json.dumps(
                {
                    "patch": {"province_name": "广西"},
                    "assistant_message": "收到",
                },
                ensure_ascii=False,
            )
        )
        empty = parse_extracted_message(
            json.dumps(
                {"patch": {}, "assistant_message": "请补充信息"},
                ensure_ascii=False,
            )
        )
        self.assertEqual(update.intent, "update_draft")
        self.assertEqual(empty.intent, "help")

    def test_guangxi_and_guilin_are_normalized_from_short_names(self):
        province_patch, clears = normalize_patch(
            {},
            TripDraftPatch(province_name="广西"),
        )
        draft = merge_draft({}, province_patch, clear_fields=clears)
        self.assertEqual(draft.province_code, "450000")
        self.assertEqual(draft.province_name, "广西壮族自治区")
        self.assertIn("哪个城市", next_question(draft.model_dump(mode="json")))

        city_patch, clears = normalize_patch(
            draft.model_dump(mode="json", exclude_none=True),
            TripDraftPatch(city_name="桂林"),
        )
        draft = merge_draft(
            draft.model_dump(mode="json", exclude_none=True),
            city_patch,
            clear_fields=clears,
        )
        self.assertEqual(draft.city_code, "450300")
        self.assertEqual(draft.city_name, "桂林市")

    def test_changing_city_can_also_change_province(self):
        current = {
            "province_code": "450000",
            "province_name": "广西壮族自治区",
            "city_code": "450300",
            "city_name": "桂林市",
        }
        patch, clears = normalize_patch(
            current,
            TripDraftPatch(city_name="成都"),
        )
        changed = merge_draft(current, patch, clear_fields=clears)
        self.assertEqual(changed.province_code, "510000")
        self.assertEqual(changed.province_name, "四川省")
        self.assertEqual(changed.city_code, "510100")
        self.assertEqual(changed.city_name, "成都市")

    def test_null_does_not_clear_but_explicit_clear_does(self):
        current = {
            "start_date": "2026-10-01",
            "end_date": "2026-10-02",
            "budget": 3000,
        }
        patch = TripDraftPatch.model_validate({"budget": None})
        unchanged = merge_draft(current, patch)
        self.assertEqual(unchanged.budget, 3000)

        cleared = merge_draft(
            current,
            TripDraftPatch(),
            clear_fields={"start_date", "end_date"},
        )
        self.assertIsNone(cleared.start_date)
        self.assertIsNone(cleared.end_date)
        self.assertEqual(cleared.budget, 3000)

    def test_interests_can_be_added_and_removed_without_replacement(self):
        draft = merge_draft(
            {"interests": ["历史文化", "美食"]},
            TripDraftPatch(interests=["自然风景"]),
            add_interests=["自然风景"],
            remove_interests=["美食"],
        )
        self.assertEqual(draft.interests, ["历史文化", "自然风景"])

    def test_colloquial_short_answers_are_recovered_conservatively(self):
        first = detect_context_patch(
            {},
            "两天，两个人，预算无线",
            today=date(2026, 8, 9),
        )
        self.assertEqual(first.duration_days, 2)
        self.assertEqual(first.people, 2)
        self.assertTrue(first.budget_flexible)

        second = detect_context_patch(
            first.model_dump(mode="json", exclude_none=True),
            "明天",
            today=date(2026, 8, 9),
        )
        draft = merge_draft(
            first.model_dump(mode="json", exclude_none=True),
            second,
        )
        self.assertEqual(draft.start_date, date(2026, 8, 10))
        self.assertEqual(draft.end_date, date(2026, 8, 11))
        self.assertEqual(detect_interest_additions("吃吃吃"), ["美食"])
        self.assertTrue(wants_generation("都可以，你安排吧"))

        later = detect_context_patch(
            {},
            "两天后出发",
            today=date(2026, 8, 9),
        )
        self.assertEqual(later.start_date, date(2026, 8, 11))
        self.assertIsNone(later.duration_days)

    def test_chinese_budget_amounts_are_recovered(self):
        self.assertEqual(
            detect_context_patch({}, "预算五百").budget,
            500,
        )
        self.assertEqual(
            detect_context_patch({}, "预算一千").budget,
            1000,
        )
        self.assertEqual(
            detect_context_patch({}, "预算两千五").budget,
            2500,
        )
        self.assertEqual(
            detect_context_patch({}, "预算5k").budget,
            5000,
        )

    def test_relative_date_pair_becomes_start_and_end(self):
        patch = detect_context_patch(
            {},
            "明天 后天",
            today=date(2026, 8, 9),
        )
        self.assertEqual(patch.start_date, date(2026, 8, 10))
        self.assertEqual(patch.end_date, date(2026, 8, 11))

        compact_patch = detect_context_patch(
            {},
            "明后天",
            today=date(2026, 8, 9),
        )
        self.assertEqual(compact_patch.start_date, date(2026, 8, 10))
        self.assertEqual(compact_patch.end_date, date(2026, 8, 11))

    def test_single_relative_date_can_complete_existing_range(self):
        patch = detect_context_patch(
            {"start_date": "2026-08-10"},
            "后天",
            today=date(2026, 8, 9),
        )
        self.assertEqual(patch.end_date, date(2026, 8, 11))
        self.assertIsNone(patch.start_date)

    def test_fixed_conversation_matrix_passes(self):
        result = evaluate_conversations()
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["passed_cases"], 4)

    def test_live_evaluator_records_extractor_failure(self):
        def failing_extractor(draft, message):
            raise RuntimeError("simulated model outage")

        result = evaluate_conversations(
            live=True,
            extractor=failing_extractor,
        )
        self.assertFalse(result["passed"])
        self.assertEqual(result["passed_cases"], 0)
        self.assertIn(
            "RuntimeError",
            result["cases"][0]["turns"][0]["failures"][0],
        )


if __name__ == "__main__":
    unittest.main()
