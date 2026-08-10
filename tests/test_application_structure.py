import unittest

from app.main import app


# === 应用装配测试：防止拆分后漏挂某个业务路由 ===
# 流程：生成 OpenAPI → 读取公开路径 → 与预期接口集合比较
class ApplicationStructureTests(unittest.TestCase):
    def test_all_public_routes_are_registered(self):
        expected_paths = {
            "/",
            "/health",
            "/auth/register",
            "/auth/login",
            "/conversations",
            "/conversations/{conversation_id}",
            "/conversations/{conversation_id}/messages",
            "/conversations/{conversation_id}/confirm",
            (
                "/conversations/{conversation_id}/draft-previews/"
                "{message_id}/apply"
            ),
            (
                "/conversations/{conversation_id}/draft-previews/"
                "{message_id}/dismiss"
            ),
            (
                "/conversations/{conversation_id}/modification-proposals/"
                "{proposal_id}/apply"
            ),
            (
                "/conversations/{conversation_id}/modification-proposals/"
                "{proposal_id}/dismiss"
            ),
            "/regions",
            "/regions/{province_code}/cities",
            "/trips",
            "/trips/{trip_id}",
            "/trips/{trip_id}/generate",
            "/trips/{trip_id}/generation-runs/latest",
            "/trips/{trip_id}/itinerary/operations",
        }

        self.assertEqual(set(app.openapi()["paths"]), expected_paths)


if __name__ == "__main__":
    unittest.main()
