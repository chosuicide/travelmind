import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth.dependencies import get_current_user
from app.db import models
from app.db.session import Base, get_db
from app.generation.service import run_generation_task
from app.generation.worker import recover_stale_runs, run_worker_once
from app.main import app


def _agent_itinerary() -> dict:
    return {
        "days": [
            {
                "day_number": 1,
                "summary": "荔湾历史文化",
                "activities": [
                    {
                        "place_provider_id": "B00140H88E",
                        "name": "陈家祠堂",
                        "location": "中山七路恩龙里34号",
                        "start_time": "09:00",
                        "end_time": "11:00",
                        "estimated_cost": 20,
                        "description": "参观岭南建筑艺术",
                        "verified_place": {
                            "amap_id": "B00140H88E",
                            "name": "陈家祠堂",
                            "address": "中山七路恩龙里34号",
                            "city": "广州市",
                            "district": "荔湾区",
                            "latitude": 23.1293,
                            "longitude": 113.2466,
                            "selection_role": "primary",
                        },
                    }
                ],
            }
        ]
    }


# === Agent 正式生成接口测试：隔离数据库验证成功保存与失败回滚 ===
# 流程：待生成 Trip → Mock 已验收 Agent 结果/异常 → API → 数据库状态断言
class ItineraryGenerationApiTests(unittest.TestCase):
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

        self.TestingSessionLocal = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
        )
        Base.metadata.create_all(bind=self.engine)

        with self.TestingSessionLocal() as db:
            owner = models.User(
                username="generation-owner",
                email="generation@example.com",
                password_hash="test-hash",
            )
            db.add(owner)
            db.flush()
            trip = models.Trip(
                user_id=owner.id,
                destination="广州",
                start_date=date(2026, 10, 1),
                end_date=date(2026, 10, 1),
                budget=1000,
                people=2,
                interests=["历史文化"],
                pace="relaxed",
                notes=None,
                status="pending",
            )
            db.add(trip)
            db.commit()
            self.owner_id = owner.id
            self.trip_id = trip.id

        def override_get_db():
            db = self.TestingSessionLocal()
            try:
                yield db
            finally:
                db.close()

        def override_current_user():
            return SimpleNamespace(id=self.owner_id)

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user] = override_current_user
        self.client = TestClient(app)

        self.quota_patcher = patch(
            "app.itinerary.router.check_generation_quota"
        )
        self.session_patcher = patch(
            "app.generation.service.SessionLocal",
            self.TestingSessionLocal,
        )
        self.worker_session_patcher = patch(
            "app.generation.worker.SessionLocal",
            self.TestingSessionLocal,
        )
        self.quota_patcher.start()
        self.session_patcher.start()
        self.worker_session_patcher.start()

    def tearDown(self):
        self.client.close()
        self.quota_patcher.stop()
        self.session_patcher.stop()
        self.worker_session_patcher.stop()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    @patch("app.generation.service.generate_itinerary_with_tools")
    def test_success_persists_agent_verified_place(self, mock_generate):
        def fake_generate(
            trip,
            on_tool_result,
            on_quality_result,
            on_model_usage,
            on_graph_event,
            graph_thread_id,
        ):
            self.assertTrue(graph_thread_id.startswith("generation-"))
            on_model_usage(
                {
                    "input_tokens": 120,
                    "output_tokens": 80,
                    "total_tokens": 200,
                }
            )
            on_tool_result(
                {
                    "tool_name": "search_places",
                    "arguments": {
                        "keywords": "陈家祠",
                        "district": "荔湾区",
                        "category": "attraction",
                        "limit": 3,
                    },
                    "candidate_count": 3,
                    "status": "succeeded",
                }
            )
            with self.TestingSessionLocal() as checkpoint_db:
                checkpoint = checkpoint_db.query(models.GenerationRun).one()
                self.assertEqual(checkpoint.status, "running")
                self.assertEqual(checkpoint.tool_call_count, 1)
                self.assertEqual(checkpoint.trace[0]["name"], "search_places")
            on_quality_result(
                {
                    "stage": "selection",
                    "selected": "repaired",
                    "original_penalty": 6.0,
                    "repaired_penalty": 3.0,
                    "remaining_warnings": [
                        {
                            "code": "transfer_distance",
                            "severity": "warning",
                            "penalty": 3.0,
                            "message": "One transfer remains longer than ideal",
                        }
                    ],
                }
            )
            return _agent_itinerary()

        mock_generate.side_effect = fake_generate

        response = self.client.post(f"/trips/{self.trip_id}/generate")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "queued")
        self.assertIn("run_id", response.json())
        mock_generate.assert_not_called()

        with self.TestingSessionLocal() as db:
            trip = db.get(models.Trip, self.trip_id)
            run = db.query(models.GenerationRun).one()
            self.assertEqual(trip.status, "generating")
            self.assertEqual(run.status, "queued")

        self.assertTrue(run_worker_once())
        mock_generate.assert_called_once()

        self.assertFalse(run_generation_task(response.json()["run_id"]))
        mock_generate.assert_called_once()

        with self.TestingSessionLocal() as db:
            trip = db.get(models.Trip, self.trip_id)
            activity = db.query(models.Activity).one()
            run = db.query(models.GenerationRun).one()
            self.assertEqual(trip.status, "generated")
            self.assertEqual(run.status, "succeeded")
            self.assertEqual(run.input_tokens, 120)
            self.assertEqual(run.output_tokens, 80)
            self.assertEqual(run.total_tokens, 200)
            self.assertEqual(run.tool_call_count, 1)
            self.assertEqual(run.trace[0]["name"], "search_places")
            self.assertNotIn("draft", run.trace[0]["arguments"])
            self.assertEqual(run.trace[1]["stage"], "selection")
            self.assertEqual(run.trace[1]["selected"], "repaired")
            self.assertEqual(
                run.trace[1]["issues"][0]["code"],
                "transfer_distance",
            )
            self.assertEqual(activity.place_provider, "amap")
            self.assertEqual(
                activity.place_provider_id,
                "B00140H88E",
            )
            self.assertEqual(activity.verified_name, "陈家祠堂")
            self.assertEqual(
                activity.verified_address,
                "中山七路恩龙里34号",
            )
            self.assertAlmostEqual(activity.latitude, 23.1293)
            self.assertAlmostEqual(activity.longitude, 113.2466)

        latest_response = self.client.get(
            f"/trips/{self.trip_id}/generation-runs/latest"
        )
        self.assertEqual(latest_response.status_code, 200)
        self.assertEqual(latest_response.json()["status"], "succeeded")
        self.assertEqual(latest_response.json()["total_tokens"], 200)

    @patch("app.generation.service.generate_itinerary_with_tools")
    def test_agent_failure_rolls_back_and_marks_trip_failed(
        self,
        mock_generate,
    ):
        mock_generate.side_effect = RuntimeError(
            "upstream request failed with key=secret-value"
        )

        response = self.client.post(f"/trips/{self.trip_id}/generate")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "queued")
        mock_generate.assert_not_called()

        with self.TestingSessionLocal() as db:
            trip = db.get(models.Trip, self.trip_id)
            run = db.query(models.GenerationRun).one()
            self.assertEqual(trip.status, "generating")
            self.assertEqual(run.status, "queued")

        self.assertTrue(run_worker_once())
        mock_generate.assert_called_once()

        with self.TestingSessionLocal() as db:
            trip = db.get(models.Trip, self.trip_id)
            run = db.query(models.GenerationRun).one()
            self.assertEqual(trip.status, "generation_failed")
            self.assertEqual(run.status, "failed")
            self.assertEqual(run.error_code, "RuntimeError")
            self.assertEqual(
                run.error_message,
                "Generation failed; inspect server logs for details",
            )
            self.assertNotIn("secret-value", run.error_message)
            self.assertEqual(db.query(models.TripDay).count(), 0)
            self.assertEqual(db.query(models.Activity).count(), 0)

    def test_worker_returns_false_when_queue_is_empty(self):
        self.assertFalse(run_worker_once())

    @patch("app.generation.worker.run_generation_task", return_value=True)
    def test_worker_selects_oldest_when_multiple_runs_are_queued(
        self,
        mock_run_task,
    ):
        with self.TestingSessionLocal() as db:
            trip = db.get(models.Trip, self.trip_id)
            trip.status = "generating"
            older = models.GenerationRun(
                trip_id=trip.id,
                user_id=self.owner_id,
                status="queued",
                model_name="test-model",
                prompt_version="test-prompt",
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                tool_call_count=0,
                trace=[],
                created_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            )
            newer = models.GenerationRun(
                trip_id=trip.id,
                user_id=self.owner_id,
                status="queued",
                model_name="test-model",
                prompt_version="test-prompt",
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                tool_call_count=0,
                trace=[],
                created_at=datetime.now(timezone.utc),
            )
            db.add_all([older, newer])
            db.commit()
            older_id = older.id

        self.assertTrue(run_worker_once())
        mock_run_task.assert_called_once_with(older_id)

    def test_stale_running_task_is_requeued_for_checkpoint_resume(self):
        with self.TestingSessionLocal() as db:
            trip = db.get(models.Trip, self.trip_id)
            trip.status = "generating"
            run = models.GenerationRun(
                trip_id=trip.id,
                user_id=self.owner_id,
                status="running",
                model_name="test-model",
                prompt_version="test-prompt",
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                tool_call_count=0,
                trace=[],
                created_at=datetime.now(timezone.utc) - timedelta(hours=2),
                started_at=datetime.now(timezone.utc) - timedelta(hours=1),
            )
            db.add(run)
            db.commit()
            run_id = run.id

        recovered = recover_stale_runs(timedelta(minutes=30))

        self.assertEqual(recovered, 1)
        with self.TestingSessionLocal() as db:
            trip = db.get(models.Trip, self.trip_id)
            run = db.get(models.GenerationRun, run_id)
            self.assertEqual(trip.status, "generating")
            self.assertEqual(run.status, "queued")
            self.assertEqual(run.error_code, "ResumingFromCheckpoint")
            self.assertIsNone(run.started_at)
            self.assertIsNone(run.finished_at)

    def test_generating_trip_cannot_create_another_run(self):
        with self.TestingSessionLocal() as db:
            trip = db.get(models.Trip, self.trip_id)
            trip.status = "generating"
            db.commit()

        response = self.client.post(f"/trips/{self.trip_id}/generate")

        self.assertEqual(response.status_code, 409)
        with self.TestingSessionLocal() as db:
            self.assertEqual(db.query(models.GenerationRun).count(), 0)
            self.assertEqual(db.query(models.GenerationUsage).count(), 0)

    def test_six_day_legacy_trip_is_rejected_before_usage_or_run(self):
        with self.TestingSessionLocal() as db:
            trip = db.get(models.Trip, self.trip_id)
            trip.end_date = trip.start_date + timedelta(days=5)
            db.commit()

        response = self.client.post(f"/trips/{self.trip_id}/generate")

        self.assertEqual(response.status_code, 422)
        self.assertIn("up to 5 days", response.json()["detail"])
        with self.TestingSessionLocal() as db:
            trip = db.get(models.Trip, self.trip_id)
            self.assertEqual(trip.status, "pending")
            self.assertEqual(db.query(models.GenerationRun).count(), 0)
            self.assertEqual(db.query(models.GenerationUsage).count(), 0)

    def test_inverted_legacy_trip_is_rejected_before_usage_or_run(self):
        with self.TestingSessionLocal() as db:
            trip = db.get(models.Trip, self.trip_id)
            trip.end_date = trip.start_date - timedelta(days=1)
            db.commit()

        response = self.client.post(f"/trips/{self.trip_id}/generate")

        self.assertEqual(response.status_code, 422)
        self.assertIn("cannot be earlier", response.json()["detail"])
        with self.TestingSessionLocal() as db:
            trip = db.get(models.Trip, self.trip_id)
            self.assertEqual(trip.status, "pending")
            self.assertEqual(db.query(models.GenerationRun).count(), 0)
            self.assertEqual(db.query(models.GenerationUsage).count(), 0)


if __name__ == "__main__":
    unittest.main()
