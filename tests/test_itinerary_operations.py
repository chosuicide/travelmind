import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth.dependencies import get_current_user
from app.db import models
from app.db.session import Base, get_db
from app.main import app


# === 行程编辑接口测试：使用隔离内存数据库和假的高德响应 ===
# 流程：准备用户与行程 → 调用真实 FastAPI 路由 → 校验响应与数据库结果
class ItineraryOperationsApiTests(unittest.TestCase):
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
                username="owner",
                email="owner@example.com",
                password_hash="test-hash",
            )
            other_user = models.User(
                username="other",
                email="other@example.com",
                password_hash="test-hash",
            )
            db.add_all([owner, other_user])
            db.flush()

            trip = models.Trip(
                user_id=owner.id,
                destination="广州",
                start_date=date(2026, 10, 1),
                end_date=date(2026, 10, 2),
                budget=5000,
                people=2,
                interests=["历史文化"],
                pace="balanced",
                notes=None,
                status="generated",
            )
            db.add(trip)
            db.flush()

            day_one = models.TripDay(
                trip_id=trip.id,
                day_number=1,
                date=trip.start_date,
                summary="第一天",
            )
            day_two = models.TripDay(
                trip_id=trip.id,
                day_number=2,
                date=trip.end_date,
                summary="第二天",
            )
            db.add_all([day_one, day_two])
            db.flush()

            activity_a = self._make_activity(
                day_id=day_one.id,
                name="广州塔",
                order=1,
            )
            activity_b = self._make_activity(
                day_id=day_one.id,
                name="广东省博物馆",
                order=2,
            )
            activity_c = self._make_activity(
                day_id=day_two.id,
                name="陈家祠",
                order=1,
            )
            db.add_all([activity_a, activity_b, activity_c])
            db.commit()

            self.owner_id = owner.id
            self.other_user_id = other_user.id
            self.trip_id = trip.id
            self.day_one_id = day_one.id
            self.day_two_id = day_two.id
            self.activity_a_id = activity_a.id
            self.activity_b_id = activity_b.id
            self.activity_c_id = activity_c.id

        self.current_user_id = self.owner_id

        def override_get_db():
            db = self.TestingSessionLocal()
            try:
                yield db
            finally:
                db.close()

        def override_current_user():
            return SimpleNamespace(id=self.current_user_id)

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[
            get_current_user
        ] = override_current_user

        self.place_patcher = patch(
            "app.itinerary.editor.search_place",
            side_effect=self._fake_search_place,
        )
        self.mock_search_place = self.place_patcher.start()
        self.route_patcher = patch(
            "app.itinerary.routes.estimate_place_route",
            side_effect=self._fake_estimate_route,
        )
        self.mock_estimate_route = self.route_patcher.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.route_patcher.stop()
        self.place_patcher.stop()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    @staticmethod
    def _make_activity(
        day_id: int,
        name: str,
        order: int,
    ) -> models.Activity:
        return models.Activity(
            trip_day_id=day_id,
            name=name,
            location="广州市",
            start_time="09:00",
            end_time="10:00",
            estimated_cost=0,
            description=f"参观{name}",
            order=order,
            place_provider="amap",
            place_provider_id=f"poi-{order}",
            verified_name=name,
            verified_address="测试地址",
            latitude=23.1291,
            longitude=113.2644,
        )

    @staticmethod
    def _fake_search_place(
        name: str,
        location: str,
        destination: str,
    ) -> dict:
        return {
            "amap_id": f"verified-{name}",
            "name": name,
            "address": f"{destination}{location}",
            "latitude": 23.1291,
            "longitude": 113.2644,
        }

    @staticmethod
    def _fake_estimate_route(
        origin: dict,
        destination: dict,
        mode: str,
    ) -> dict:
        return {
            "origin_place_id": origin["amap_id"],
            "origin_name": origin["name"],
            "destination_place_id": destination["amap_id"],
            "destination_name": destination["name"],
            "mode": mode,
            "distance_meters": 800,
            "duration_minutes": 12.0,
            "estimated_cost": 0.0,
            "walking_distance_meters": 800,
            "polyline": [
                [113.2644, 23.1291],
                [113.2645, 23.1292],
            ],
        }

    def _post_operations(self, operations: list[dict]):
        return self.client.post(
            f"/trips/{self.trip_id}/itinerary/operations",
            json={"operations": operations},
        )

    def test_add_update_move_and_remove_activity(self):
        add_response = self._post_operations(
            [
                {
                    "type": "add_activity",
                    "day_id": self.day_one_id,
                    "order": 2,
                    "activity": {
                        "name": "沙面岛",
                        "location": "荔湾区",
                        "start_time": "10:30",
                        "end_time": "12:00",
                        "estimated_cost": 20,
                        "description": "散步",
                    },
                }
            ]
        )

        self.assertEqual(add_response.status_code, 200)
        add_data = add_response.json()
        added_id = add_data["applied_operations"][0]["activity_id"]
        day_one = add_data["trip"]["days"][0]
        self.assertEqual(
            [activity["name"] for activity in day_one["activities"]],
            ["广州塔", "沙面岛", "广东省博物馆"],
        )
        self.assertEqual(
            [activity["order"] for activity in day_one["activities"]],
            [1, 2, 3],
        )

        update_response = self._post_operations(
            [
                {
                    "type": "update_activity",
                    "activity_id": added_id,
                    "changes": {
                        "name": "沙面",
                        "start_time": "13:00",
                        "end_time": "14:30",
                        "description": None,
                    },
                }
            ]
        )

        self.assertEqual(update_response.status_code, 200)
        updated_activities = update_response.json()["trip"]["days"][0][
            "activities"
        ]
        updated_activity = next(
            item for item in updated_activities if item["id"] == added_id
        )
        self.assertEqual(updated_activity["name"], "沙面")
        self.assertIsNone(updated_activity["description"])
        self.assertEqual(
            updated_activity["verified_place"]["provider_id"],
            "verified-沙面",
        )

        move_response = self._post_operations(
            [
                {
                    "type": "move_activity",
                    "activity_id": added_id,
                    "target_day_id": self.day_two_id,
                    "target_order": 1,
                }
            ]
        )

        self.assertEqual(move_response.status_code, 200)
        moved_days = move_response.json()["trip"]["days"]
        self.assertEqual(
            [item["name"] for item in moved_days[0]["activities"]],
            ["广州塔", "广东省博物馆"],
        )
        self.assertEqual(
            [item["name"] for item in moved_days[1]["activities"]],
            ["沙面", "陈家祠"],
        )

        remove_response = self._post_operations(
            [
                {
                    "type": "remove_activity",
                    "activity_id": self.activity_b_id,
                }
            ]
        )

        self.assertEqual(remove_response.status_code, 200)
        final_day_one = remove_response.json()["trip"]["days"][0]
        self.assertEqual(
            [item["name"] for item in final_day_one["activities"]],
            ["广州塔"],
        )
        self.assertEqual(final_day_one["activities"][0]["order"], 1)

    def test_other_user_cannot_edit_trip(self):
        self.current_user_id = self.other_user_id

        response = self._post_operations(
            [
                {
                    "type": "remove_activity",
                    "activity_id": self.activity_a_id,
                }
            ]
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Trip not found")

    def test_activity_can_be_reordered_inside_the_same_day(self):
        response = self._post_operations(
            [
                {
                    "type": "move_activity",
                    "activity_id": self.activity_b_id,
                    "target_day_id": self.day_one_id,
                    "target_order": 1,
                }
            ]
        )

        self.assertEqual(response.status_code, 200)
        activities = response.json()["trip"]["days"][0]["activities"]
        self.assertEqual(
            [item["name"] for item in activities],
            ["广东省博物馆", "广州塔"],
        )
        self.assertEqual(
            [item["order"] for item in activities],
            [1, 2],
        )

    def test_batch_rolls_back_when_later_operation_fails(self):
        response = self._post_operations(
            [
                {
                    "type": "update_activity",
                    "activity_id": self.activity_a_id,
                    "changes": {"description": "不应该被保存"},
                },
                {
                    "type": "remove_activity",
                    "activity_id": 999_999,
                },
            ]
        )

        self.assertEqual(response.status_code, 404)

        with self.TestingSessionLocal() as db:
            activity = db.get(models.Activity, self.activity_a_id)
            self.assertEqual(activity.description, "参观广州塔")

    def test_invalid_time_order_is_rejected(self):
        response = self._post_operations(
            [
                {
                    "type": "update_activity",
                    "activity_id": self.activity_a_id,
                    "changes": {
                        "start_time": "18:00",
                        "end_time": "09:00",
                    },
                }
            ]
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["detail"],
            "start_time must be earlier than end_time",
        )

    def test_unverified_new_place_is_rejected(self):
        self.mock_search_place.side_effect = None
        self.mock_search_place.return_value = None

        response = self._post_operations(
            [
                {
                    "type": "add_activity",
                    "day_id": self.day_one_id,
                    "activity": {
                        "name": "不存在的景点",
                        "location": "广州",
                    },
                }
            ]
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["detail"],
            "Unverified place: 不存在的景点",
        )


if __name__ == "__main__":
    unittest.main()
