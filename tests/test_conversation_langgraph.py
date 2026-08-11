import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from langchain_core.messages import AIMessage
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.conversations.agent.graph import run_conversation_agent
from app.db import models
from app.db.session import Base
from app.modifications.schemas import ModificationAgentResponse


class ScriptedChatModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.bound_tools = None
        self.bound_kwargs = None

    def bind_tools(self, tools, **kwargs):
        self.bound_tools = tools
        self.bound_kwargs = kwargs
        return self

    def invoke(self, messages):
        return self.responses.pop(0)


# === LangGraph 会话测试：验证模型决策、工具执行和预览自动过期 ===
# 流程：AI tool_call → 后端合法写入 → revision 增长 → AI读取结果后回复
class ConversationLangGraphTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        self.Session = sessionmaker(bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
        with self.Session() as db:
            user = models.User(
                username="graph-user",
                email="graph@example.com",
                password_hash="hash",
            )
            db.add(user)
            db.flush()
            conversation = models.Conversation(
                user_id=user.id,
                status="collecting",
                draft={
                    "province_code": "410000",
                    "province_name": "河南省",
                    "city_code": "410400",
                    "city_name": "平顶山市",
                },
                draft_revision=0,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            db.add(conversation)
            db.flush()
            preview = models.ChatMessage(
                conversation_id=conversation.id,
                role="assistant",
                message_type="requirements",
                content="旧预览",
                payload={
                    "kind": "draft_preview",
                    "status": "pending",
                    "base_revision": 0,
                    "candidate_draft": conversation.draft,
                },
                created_at=datetime.now(timezone.utc),
            )
            db.add(preview)
            db.commit()
            self.user_id = user.id
            self.conversation_id = conversation.id
            self.preview_id = preview.id

    def tearDown(self):
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_agent_updates_context_and_stales_old_preview(self):
        model = ScriptedChatModel(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "update_trip_context",
                            "args": {"patch": {"budget": 1000, "people": 3}},
                            "id": "call-update-1",
                            "type": "tool_call",
                        }
                    ],
                    usage_metadata={
                        "input_tokens": 20,
                        "output_tokens": 8,
                        "total_tokens": 28,
                    },
                ),
                AIMessage(
                    content="预算和人数已经调整好了，旧方案也已自动更新状态。",
                    usage_metadata={
                        "input_tokens": 25,
                        "output_tokens": 12,
                        "total_tokens": 37,
                    },
                ),
            ]
        )
        with self.Session() as db:
            conversation = db.get(models.Conversation, self.conversation_id)
            result = run_conversation_agent(
                db,
                conversation,
                "改成三个人，预算一千",
                model=model,
            )
            db.flush()
            preview = db.get(models.ChatMessage, self.preview_id)

            self.assertTrue(result.accepted)
            self.assertEqual(conversation.draft["people"], 3)
            self.assertEqual(conversation.draft["budget"], 1000)
            self.assertEqual(conversation.draft_revision, 1)
            self.assertEqual(preview.payload["status"], "stale")
            self.assertEqual(result.usage["total_tokens"], 65)
            self.assertEqual(len(model.bound_tools), 3)

    @patch("app.conversations.agent.tools.rebuild_trip_routes")
    @patch("app.integrations.deepseek.generate_modification_response")
    def test_generated_trip_requires_an_explicit_propose_or_reply_action(
        self,
        mock_generate_modification,
        mock_rebuild_routes,
    ):
        mock_generate_modification.return_value = ModificationAgentResponse(
            action="proposal",
            assistant_message="我整理了一份修改预览。",
            operations=[
                {
                    "type": "update_activity",
                    "activity_id": 1,
                    "changes": {"description": "慢慢游览，不要太赶"},
                }
            ],
        )
        with self.Session() as db:
            trip = models.Trip(
                user_id=self.user_id,
                destination="河南省平顶山市",
                start_date=date(2026, 8, 10),
                end_date=date(2026, 8, 10),
                budget=1000,
                people=2,
                interests=["历史文化"],
                pace="relaxed",
                status="generated",
            )
            db.add(trip)
            db.flush()
            trip_day = models.TripDay(
                trip_id=trip.id,
                day_number=1,
                date=trip.start_date,
                summary="平顶山历史文化",
            )
            db.add(trip_day)
            db.flush()
            activity = models.Activity(
                trip_day_id=trip_day.id,
                name="香山寺",
                location="新华区",
                start_time="09:00",
                end_time="11:00",
                estimated_cost=20,
                description="参观寺院",
                order=1,
            )
            db.add(activity)
            db.flush()
            mock_generate_modification.return_value.operations[0].activity_id = (
                activity.id
            )
            conversation = db.get(models.Conversation, self.conversation_id)
            conversation.trip_id = trip.id
            conversation.status = "generated"
            db.flush()

            propose_model = ScriptedChatModel(
                [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "propose_itinerary_modification",
                                "args": {"request": "把香山寺安排得轻松一点"},
                                "id": "call-propose-1",
                                "type": "tool_call",
                            }
                        ],
                    ),
                    AIMessage(content="修改预览已经整理好，你可以继续调整或确认。"),
                ]
            )
            proposed = run_conversation_agent(
                db,
                conversation,
                "把香山寺安排得轻松一点",
                model=propose_model,
            )
            db.flush()

            self.assertEqual(
                {
                    tool["function"]["name"]
                    for tool in propose_model.bound_tools
                },
                {
                    "propose_itinerary_modification",
                    "reply_to_generated_trip",
                },
            )
            self.assertEqual(
                propose_model.bound_kwargs["tool_choice"],
                "required",
            )
            self.assertEqual(len(propose_model.bound_tools), 2)
            self.assertEqual(proposed.proposal_payload["status"], "pending")
            self.assertEqual(activity.description, "参观寺院")

            confirmed = run_conversation_agent(
                db,
                conversation,
                "确认修改",
                model=ScriptedChatModel([]),
            )
            db.flush()

            self.assertTrue(confirmed.trip_changed)
            self.assertEqual(confirmed.proposal_payload["status"], "applied")
            self.assertEqual(activity.description, "慢慢游览，不要太赶")
            mock_rebuild_routes.assert_called_once()


if __name__ == "__main__":
    unittest.main()
