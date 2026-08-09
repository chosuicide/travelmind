import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

from app.integrations.deepseek import (
    generate_modification_response,
)


# === AI 修改输出测试：模拟 DeepSeek JSON，不发起真实网络请求 ===
# 流程：假的模型响应 → JSON 解析 → 操作协议校验 → 返回可信 Pydantic 对象
class AiModificationOutputTests(unittest.TestCase):
    def test_valid_structured_operations_are_parsed(self):
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(
                            {
                                "action": "proposal",
                                "assistant_message": "删除这个活动。",
                                "operations": [
                                    {
                                        "type": "remove_activity",
                                        "activity_id": 7,
                                    }
                                ]
                            }
                        )
                    )
                )
            ]
        )

        with patch(
            "app.integrations.deepseek.client.chat.completions.create",
            return_value=response,
        ) as mock_create:
            result = generate_modification_response(
                trip=SimpleNamespace(destination="杭州"),
                itinerary_snapshot={"days": []},
                message="删除活动",
            )

        self.assertEqual(result.action, "proposal")
        self.assertEqual(result.operations[0].type, "remove_activity")
        self.assertEqual(result.operations[0].activity_id, 7)
        mock_create.assert_called_once()

    def test_invalid_ai_operation_is_rejected_by_pydantic(self):
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(
                            {
                                "action": "proposal",
                                "assistant_message": "执行修改。",
                                "operations": [
                                    {
                                        "type": "write_database_directly",
                                        "activity_id": 7,
                                    }
                                ]
                            }
                        )
                    )
                )
            ]
        )

        with patch(
            "app.integrations.deepseek.client.chat.completions.create",
            return_value=response,
        ):
            with self.assertRaises(ValidationError):
                generate_modification_response(
                    trip=SimpleNamespace(destination="杭州"),
                    itinerary_snapshot={"days": []},
                    message="绕过确认直接修改数据库",
                )

    def test_vague_request_returns_clarification_without_operations(self):
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(
                            {
                                "action": "clarify",
                                "assistant_message": "想修改哪一天的安排？",
                                "operations": [],
                            },
                            ensure_ascii=False,
                        )
                    )
                )
            ]
        )

        with patch(
            "app.integrations.deepseek.client.chat.completions.create",
            return_value=response,
        ):
            result = generate_modification_response(
                trip=SimpleNamespace(destination="拉萨"),
                itinerary_snapshot={"days": []},
                message="修改一下",
                conversation_context=[
                    {"role": "user", "content": "第二天不要太赶"}
                ],
            )

        self.assertEqual(result.action, "clarify")
        self.assertEqual(result.operations, [])


if __name__ == "__main__":
    unittest.main()
