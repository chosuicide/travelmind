import unittest
from datetime import date
from unittest.mock import patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import models
from app.db.session import Base
from app.itinerary.routes import rebuild_trip_routes
from app.trips.service import serialize_trip


class TripRoutePersistenceTests(unittest.TestCase):
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

        self.SessionLocal = sessionmaker(bind=self.engine)
        Base.metadata.create_all(bind=self.engine)

    def tearDown(self):
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def _create_trip(self, db):
        user = models.User(
            username="route-owner",
            email="route@example.com",
            password_hash="test-hash",
        )
        db.add(user)
        db.flush()
        trip = models.Trip(
            user_id=user.id,
            destination="广州",
            start_date=date(2026, 10, 1),
            end_date=date(2026, 10, 1),
            budget=3000,
            people=2,
            interests=["人文", "美食"],
            pace="relaxed",
            notes=None,
            status="generated",
        )
        db.add(trip)
        db.flush()
        trip_day = models.TripDay(
            trip_id=trip.id,
            day_number=1,
            date=trip.start_date,
            summary="西关与老城",
        )
        db.add(trip_day)
        db.flush()

        places = [
            ("poi-1", "陈家祠", 113.2466, 23.1293),
            ("poi-2", "永庆坊", 113.2470, 23.1295),
            ("poi-3", "沙面岛", 113.3000, 23.1300),
        ]
        for order, (place_id, name, longitude, latitude) in enumerate(
            places,
            start=1,
        ):
            db.add(
                models.Activity(
                    trip_day_id=trip_day.id,
                    name=name,
                    location=f"{name}地址",
                    start_time=f"{8 + order:02d}:00",
                    end_time=f"{9 + order:02d}:00",
                    estimated_cost=0,
                    description=name,
                    order=order,
                    place_provider="amap",
                    place_provider_id=place_id,
                    verified_name=name,
                    verified_address=f"{name}地址",
                    latitude=latitude,
                    longitude=longitude,
                )
            )
        db.flush()
        return trip

    # === 路线持久化测试：真实路线段、每日汇总和前端轨迹一次验证 ===
    # 流程：三活动 → 两段高德结果 → TripLeg → serialize_trip 路线响应
    @patch("app.itinerary.routes.estimate_place_route")
    def test_rebuilds_and_serializes_complete_day_routes(
        self,
        mock_estimate,
    ):
        def fake_estimate(origin, destination, mode):
            distance = 1200 if mode == "walking" else 5200
            duration = 18.0 if mode == "walking" else 24.0
            return {
                "origin_place_id": origin["amap_id"],
                "origin_name": origin["name"],
                "destination_place_id": destination["amap_id"],
                "destination_name": destination["name"],
                "mode": mode,
                "distance_meters": distance,
                "duration_minutes": duration,
                "estimated_cost": 0.0 if mode == "walking" else 18.0,
                "walking_distance_meters": (
                    distance if mode == "walking" else None
                ),
                "polyline": [
                    [origin["longitude"], origin["latitude"]],
                    [destination["longitude"], destination["latitude"]],
                ],
            }

        mock_estimate.side_effect = fake_estimate

        with self.SessionLocal() as db:
            trip = self._create_trip(db)
            saved_legs = rebuild_trip_routes(db, trip)
            db.commit()

            self.assertEqual(len(saved_legs), 2)
            self.assertEqual(
                [call.args[2] for call in mock_estimate.call_args_list],
                ["walking", "driving"],
            )
            self.assertEqual(db.query(models.TripLeg).count(), 2)

            payload = serialize_trip(trip, db)
            day = payload["days"][0]
            self.assertEqual(len(day["route_legs"]), 2)
            self.assertEqual(
                day["route_legs"][0]["polyline"],
                [[113.2466, 23.1293], [113.247, 23.1295]],
            )
            self.assertEqual(
                day["route_summary"],
                {
                    "leg_count": 2,
                    "is_complete": True,
                    "total_distance_meters": 6400,
                    "total_duration_minutes": 42.0,
                    "estimated_cost": 18.0,
                },
            )

            rebuild_trip_routes(db, trip)
            db.commit()
            self.assertEqual(db.query(models.TripLeg).count(), 2)

    @patch("app.itinerary.routes.estimate_place_route")
    def test_legacy_unverified_activities_remain_editable(
        self,
        mock_estimate,
    ):
        with self.SessionLocal() as db:
            trip = self._create_trip(db)
            for activity in db.query(models.Activity).all():
                activity.place_provider = None
                activity.place_provider_id = None
                activity.latitude = None
                activity.longitude = None
            db.flush()

            saved_legs = rebuild_trip_routes(db, trip)
            db.commit()

            self.assertEqual(saved_legs, [])
            mock_estimate.assert_not_called()
            payload = serialize_trip(trip, db)
            summary = payload["days"][0]["route_summary"]
            self.assertEqual(summary["leg_count"], 0)
            self.assertFalse(summary["is_complete"])


if __name__ == "__main__":
    unittest.main()
