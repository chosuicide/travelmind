from sqlalchemy.orm import Session

from app.db import models
from app.trips.schemas import TripCreate


# === Trip 创建：让表单入口和聊天确认入口共用同一套落库逻辑 ===
# 流程：已验证 TripCreate → ORM Trip → flush → 交给调用方决定事务
def create_trip_record(
    db: Session,
    user_id: int,
    trip_input: TripCreate,
) -> models.Trip:
    trip = models.Trip(
        user_id=user_id,
        destination=trip_input.destination,
        start_date=trip_input.start_date,
        end_date=trip_input.end_date,
        budget=trip_input.budget,
        people=trip_input.people,
        interests=trip_input.interests,
        pace=trip_input.pace,
        notes=trip_input.notes,
        status="created",
    )
    db.add(trip)
    db.flush()
    return trip


def get_owned_trip(
    db: Session,
    trip_id: int,
    user_id: int,
) -> models.Trip | None:
    return (
        db.query(models.Trip)
        .filter(
            models.Trip.id == trip_id,
            models.Trip.user_id == user_id,
        )
        .first()
    )


# === Trip 聚合响应：统一组装 Day、Activity 和真实 POI ===
# 流程：Trip → Days → Activities → verified_place → 完整 JSON
def serialize_trip(
    trip: models.Trip,
    db: Session,
) -> dict:
    trip_days = (
        db.query(models.TripDay)
        .filter(models.TripDay.trip_id == trip.id)
        .order_by(models.TripDay.day_number)
        .all()
    )

    days_response = []

    for day in trip_days:
        activities = (
            db.query(models.Activity)
            .filter(models.Activity.trip_day_id == day.id)
            .order_by(models.Activity.order)
            .all()
        )
        route_legs = (
            db.query(models.TripLeg)
            .filter(models.TripLeg.trip_day_id == day.id)
            .order_by(models.TripLeg.order)
            .all()
        )
        duration_values = [
            leg.duration_minutes
            for leg in route_legs
            if leg.duration_minutes is not None
        ]
        cost_values = [
            leg.estimated_cost
            for leg in route_legs
            if leg.estimated_cost is not None
        ]
        days_response.append(
            {
                "id": day.id,
                "day_number": day.day_number,
                "date": day.date,
                "summary": day.summary,
                "activities": [
                    {
                        "id": activity.id,
                        "name": activity.name,
                        "location": activity.location,
                        "start_time": activity.start_time,
                        "end_time": activity.end_time,
                        "estimated_cost": activity.estimated_cost,
                        "description": activity.description,
                        "order": activity.order,
                        "verified_place": (
                            {
                                "provider": activity.place_provider,
                                "provider_id": activity.place_provider_id,
                                "name": activity.verified_name,
                                "address": activity.verified_address,
                                "latitude": activity.latitude,
                                "longitude": activity.longitude,
                            }
                            if activity.place_provider is not None
                            else None
                        ),
                    }
                    for activity in activities
                ],
                "route_legs": [
                    {
                        "id": leg.id,
                        "order": leg.order,
                        "origin_activity_id": leg.origin_activity_id,
                        "destination_activity_id": (
                            leg.destination_activity_id
                        ),
                        "mode": leg.mode,
                        "distance_meters": leg.distance_meters,
                        "duration_minutes": leg.duration_minutes,
                        "estimated_cost": leg.estimated_cost,
                        "walking_distance_meters": (
                            leg.walking_distance_meters
                        ),
                        "polyline": leg.polyline,
                        "provider": leg.provider,
                    }
                    for leg in route_legs
                ],
                "route_summary": {
                    "leg_count": len(route_legs),
                    "is_complete": len(route_legs)
                    == max(len(activities) - 1, 0),
                    "total_distance_meters": sum(
                        leg.distance_meters for leg in route_legs
                    ),
                    "total_duration_minutes": (
                        round(sum(duration_values), 1)
                        if duration_values
                        else None
                    ),
                    "estimated_cost": (
                        round(sum(cost_values), 2)
                        if cost_values
                        else None
                    ),
                },
            }
        )

    return {
        "id": trip.id,
        "destination": trip.destination,
        "start_date": trip.start_date,
        "end_date": trip.end_date,
        "budget": trip.budget,
        "people": trip.people,
        "interests": trip.interests,
        "pace": trip.pace,
        "notes": trip.notes,
        "status": trip.status,
        "days": days_response,
    }
