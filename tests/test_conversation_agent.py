import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.conversations.extractor import (
    extract_message,
    generate_grounded_reply,
    sanitize_agent_reply,
)


def model_response(content=None, tool_calls=None, tokens=(10, 5, 15)):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=content,
                    tool_calls=tool_calls or [],
                )
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=tokens[0],
            completion_tokens=tokens[1],
            total_tokens=tokens[2],
        ),
    )


# === 模块：自由对话 Agent 回归测试 ===
# 流程：自然回答不调用工具；明确需求 → 工具决策 → 后端执行 → 权威状态回复
class ConversationAgentTests(unittest.TestCase):
    def test_question_can_receive_free_text_without_json_contract(self):
        create = Mock(
            return_value=model_response(
                content="如果更喜欢山水和慢节奏，桂林会比南宁更适合。"
            )
        )
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

        with patch("app.conversations.extractor.client", fake_client):
            result = extract_message({}, "桂林和南宁哪个更适合慢慢玩？")

        self.assertEqual(result.intent, "help")
        self.assertEqual(result.patch.model_dump(exclude_none=True), {})
        self.assertIn("桂林", result.assistant_message)
        request = create.call_args.kwargs
        self.assertNotIn("response_format", request)
        self.assertEqual(request["tool_choice"], "auto")

    def test_agent_may_update_many_requirements_then_reply_naturally(self):
        arguments = json.dumps(
            {
                "patch": {
                    "city_name": "桂林",
                    "budget": 3000,
                    "people": 2,
                    "pace": "relaxed",
                },
                "add_interests": ["自然风景"],
            },
            ensure_ascii=False,
        )
        tool_call = SimpleNamespace(
            id="call-1",
            function=SimpleNamespace(
                name="update_trip_draft",
                arguments=arguments,
            ),
        )
        create = Mock(return_value=model_response(tool_calls=[tool_call]))
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

        with patch("app.conversations.extractor.client", fake_client):
            result = extract_message(
                {},
                "我们两个人想去桂林看山水，预算三千，不要太赶。",
                history=[{"role": "assistant", "content": "想去哪里？"}],
            )

        self.assertEqual(result.intent, "update_draft")
        self.assertEqual(result.patch.city_name, "桂林")
        self.assertEqual(result.patch.people, 2)
        self.assertEqual(result.patch.pace, "relaxed")
        self.assertEqual(result.add_interests, ["自然风景"])
        self.assertEqual(create.call_count, 1)
        self.assertIn("tool_choice", create.call_args.kwargs)
        self.assertIn("tools", create.call_args.kwargs)
        self.assertEqual(result._usage["total_tokens"], 15)

    def test_agent_can_request_generation_as_a_real_tool_action(self):
        tool_call = SimpleNamespace(
            id="call-start",
            function=SimpleNamespace(
                name="start_itinerary_generation",
                arguments="{}",
            ),
        )
        create = Mock(return_value=model_response(tool_calls=[tool_call]))
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

        with patch("app.conversations.extractor.client", fake_client):
            result = extract_message(
                {"city_name": "广州市"},
                "就这样，你安排吧",
            )

        self.assertTrue(result.start_generation)
        self.assertEqual(result.intent, "help")
        self.assertEqual(len(create.call_args_list[0].kwargs["tools"]), 3)

    def test_agent_can_delegate_remaining_choices_to_preview_tool(self):
        preview_call = SimpleNamespace(
            id="call-preview",
            function=SimpleNamespace(
                name="prepare_trip_preview",
                arguments=json.dumps({"use_defaults": True}),
            ),
        )
        create = Mock(return_value=model_response(tool_calls=[preview_call]))
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

        with patch("app.conversations.extractor.client", fake_client):
            result = extract_message(
                {"city_name": "湖州市"},
                "剩下的随便，你安排吧",
            )

        self.assertTrue(result.prepare_preview)
        self.assertTrue(result.use_defaults)

    def test_grounded_reply_receives_real_backend_state(self):
        create = Mock(
            return_value=model_response(
                content="好的，湖州两日行已经整理成预览，AI 默认项可以调整。"
            )
        )
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

        with patch("app.conversations.extractor.client", fake_client):
            reply, usage = generate_grounded_reply(
                {"city_name": "湖州市", "people": 1},
                "随便",
                accepted=False,
                missing_fields=[],
                preview={"assumed_fields": ["people", "budget"]},
            )

        self.assertIn("湖州", reply)
        self.assertEqual(usage["total_tokens"], 15)
        observation = create.call_args.kwargs["messages"][0]["content"]
        self.assertIn('"city_name": "湖州市"', observation)
        self.assertIn('"people": 1', observation)

    def test_dsml_protocol_and_markdown_never_reach_the_user(self):
        leaked = (
            "已保存 **两个人**。\n\n"
            "<｜｜DSML｜｜tool_calls>\n"
            "<｜｜DSML｜｜invoke name=\"start_itinerary_generation\">\n"
            "</｜｜DSML｜｜invoke>\n"
            "</｜｜DSML｜｜tool_calls>"
        )
        self.assertEqual(
            sanitize_agent_reply(leaked),
            "需求已保存，正在开始生成行程。",
        )

        arguments = json.dumps({"patch": {"people": 2}}, ensure_ascii=False)
        update_call = SimpleNamespace(
            id="call-update",
            function=SimpleNamespace(
                name="update_trip_draft",
                arguments=arguments,
            ),
        )
        start_call = SimpleNamespace(
            id="call-start",
            function=SimpleNamespace(
                name="start_itinerary_generation",
                arguments="{}",
            ),
        )
        create = Mock(
            return_value=model_response(tool_calls=[update_call, start_call])
        )
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

        with patch("app.conversations.extractor.client", fake_client):
            result = extract_message({}, "两个人，按这个来")

        self.assertTrue(result.start_generation)
        self.assertNotIn("DSML", result.assistant_message)
        self.assertNotIn("**", result.assistant_message)


if __name__ == "__main__":
    unittest.main()
