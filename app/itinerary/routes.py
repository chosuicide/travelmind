import math

from sqlalchemy.orm import Session

from app.db import models
from app.integrations.amap import estimate_place_route


WALKING_THRESHOLD_METERS = 2_000


class RouteCalculationUnavailable(RuntimeError):
    pass


def _straight_line_distance_meters(
    origin: models.Activity,
    destination: models.Activity,
) -> float:
    if (
        origin.latitude is None
        or origin.longitude is None
        or destination.latitude is None
        or destination.longitude is None
    ):
        raise RouteCalculationUnavailable(
            "Verified activity coordinates are required for routing"
        )

    origin_latitude = math.radians(origin.latitude)
    destination_latitude = math.radians(destination.latitude)
    latitude_delta = math.radians(
        destination.latitude - origin.latitude
    )
    longitude_delta = math.radians(
        destination.longitude - origin.longitude
    )
    haversine = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(origin_latitude)
        * math.cos(destination_latitude)
        * math.sin(longitude_delta / 2) ** 2
    )
    haversine = min(max(haversine, 0.0), 1.0)
    return 6_371_000 * 2 * math.atan2(
        math.sqrt(haversine),
        math.sqrt(1 - haversine),
    )


def _choose_route_mode(
    origin: models.Activity,
    destination: models.Activity,
) -> str:
    distance = _straight_line_distance_meters(origin, destination)
    return "walking" if distance <= WALKING_THRESHOLD_METERS else "driving"


def _route_place(activity: models.Activity) -> dict:
    if activity.place_provider_id is None:
        raise RouteCalculationUnavailable(
            "Verified activity provider IDs are required for routing"
        )
    return {
        "amap_id": activity.place_provider_id,
        "name": activity.verified_name or activity.name,
        "latitude": activity.latitude,
        "longitude": activity.longitude,
    }


def _has_verified_route_data(activity: models.Activity) -> bool:
    return (
        activity.place_provider == "amap"
        and activity.place_provider_id is not None
        and activity.latitude is not None
        and activity.longitude is not None
    )


# === 路线补全服务：为最终行程的每对相邻活动保存可绘制路线 ===
# 流程：读取 Day/Activity → 选择方式 → 高德 cost+polyline → 重建 TripLeg
def rebuild_trip_routes(
    db: Session,
    trip: models.Trip,
) -> list[models.TripLeg]:
    trip_days = (
        db.query(models.TripDay)
        .filter(models.TripDay.trip_id == trip.id)
        .order_by(models.TripDay.day_number)
        .all()
    )
    day_ids = [day.id for day in trip_days]
    if day_ids:
        existing_legs = (
            db.query(models.TripLeg)
            .filter(models.TripLeg.trip_day_id.in_(day_ids))
            .all()
        )
        for existing_leg in existing_legs:
            db.delete(existing_leg)
        db.flush()

    saved_legs = []
    for trip_day in trip_days:
        activities = (
            db.query(models.Activity)
            .filter(models.Activity.trip_day_id == trip_day.id)
            .order_by(models.Activity.order)
            .all()
        )
        for leg_order, (origin, destination) in enumerate(
            zip(activities, activities[1:]),
            start=1,
        ):
            if not (
                _has_verified_route_data(origin)
                and _has_verified_route_data(destination)
            ):
                continue
            mode = _choose_route_mode(origin, destination)
            try:
                route = estimate_place_route(
                    _route_place(origin),
                    _route_place(destination),
                    mode,
                )
            except RouteCalculationUnavailable:
                raise
            except Exception as exc:
                raise RouteCalculationUnavailable(
                    "Failed to calculate route between "
                    f"{origin.name} and {destination.name}"
                ) from exc

            polyline = route.get("polyline") or []
            if len(polyline) < 2:
                raise RouteCalculationUnavailable(
                    "AMap returned no drawable route between "
                    f"{origin.name} and {destination.name}"
                )

            leg = models.TripLeg(
                trip_day_id=trip_day.id,
                origin_activity_id=origin.id,
                destination_activity_id=destination.id,
                order=leg_order,
                mode=route["mode"],
                distance_meters=route["distance_meters"],
                duration_minutes=route.get("duration_minutes"),
                estimated_cost=route.get("estimated_cost"),
                walking_distance_meters=route.get(
                    "walking_distance_meters"
                ),
                polyline=polyline,
                provider="amap",
            )
            db.add(leg)
            saved_legs.append(leg)

    db.flush()
    return saved_legs
