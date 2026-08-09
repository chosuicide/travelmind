from sqlalchemy.orm import Session

from app.db import models
from app.integrations.amap import search_place
from app.itinerary.schemas import (
    AddActivityOperation,
    ItineraryOperation,
    MoveActivityOperation,
    RemoveActivityOperation,
    UpdateActivityOperation,
)


class ItineraryEditError(Exception):
    """行程编辑业务错误的基类。"""


class ItineraryResourceNotFound(ItineraryEditError):
    """资源不存在，或资源不属于当前行程。"""


class ItineraryValidationError(ItineraryEditError):
    """操作结构合法，但不符合行程业务规则。"""


class PlaceVerificationUnavailable(ItineraryEditError):
    """高德地点服务当前无法完成验证。"""


# === 行程资源查询：子资源必须从当前 Trip 边界内查找 ===
# 流程：Trip ID + 子资源 ID → 联表确认归属 → 返回资源或 404 语义
def _get_trip_day(
    db: Session,
    trip_id: int,
    day_id: int,
) -> models.TripDay:
    trip_day = (
        db.query(models.TripDay)
        .filter(
            models.TripDay.id == day_id,
            models.TripDay.trip_id == trip_id,
        )
        .first()
    )

    if trip_day is None:
        raise ItineraryResourceNotFound("Trip day not found")

    return trip_day


def _get_activity(
    db: Session,
    trip_id: int,
    activity_id: int,
) -> models.Activity:
    activity = (
        db.query(models.Activity)
        .join(
            models.TripDay,
            models.Activity.trip_day_id == models.TripDay.id,
        )
        .filter(
            models.Activity.id == activity_id,
            models.TripDay.trip_id == trip_id,
        )
        .first()
    )

    if activity is None:
        raise ItineraryResourceNotFound("Activity not found")

    return activity


def _get_day_activities(
    db: Session,
    day_id: int,
) -> list[models.Activity]:
    return (
        db.query(models.Activity)
        .filter(models.Activity.trip_day_id == day_id)
        .order_by(models.Activity.order)
        .all()
    )


# === 活动排序：先写唯一临时负数，再写最终连续序号 ===
# 流程：现有顺序 → ID 临时负数 → flush → 1..N → flush
def _renumber_day(
    db: Session,
    day_id: int,
    activities: list[models.Activity],
):
    for activity in activities:
        activity.trip_day_id = day_id
        activity.order = -(1_000_000 + activity.id)

    db.flush()

    for index, activity in enumerate(activities, start=1):
        activity.order = index

    db.flush()


def _validate_activity_times(
    start_time: str | None,
    end_time: str | None,
):
    if start_time is not None and end_time is not None:
        if start_time >= end_time:
            raise ItineraryValidationError(
                "start_time must be earlier than end_time"
            )


# === 地点验证：地点变化后重新绑定真实高德 POI ===
# 流程：活动名称和区域 → 高德查询 → 真实 POI → 更新验证字段
def _verify_place(
    name: str,
    location: str,
    destination: str,
) -> dict:
    try:
        verified_place = search_place(
            name=name,
            location=location,
            destination=destination,
        )
    except Exception as exc:
        raise PlaceVerificationUnavailable(
            "Failed to verify place"
        ) from exc

    if verified_place is None:
        raise ItineraryValidationError(
            f"Unverified place: {name}"
        )

    return verified_place


def _set_verified_place(
    activity: models.Activity,
    verified_place: dict,
):
    activity.place_provider = "amap"
    activity.place_provider_id = verified_place.get("amap_id")
    activity.verified_name = verified_place.get("name")
    activity.verified_address = verified_place.get("address")
    activity.latitude = verified_place.get("latitude")
    activity.longitude = verified_place.get("longitude")


def _add_activity(
    db: Session,
    trip: models.Trip,
    operation: AddActivityOperation,
) -> dict:
    trip_day = _get_trip_day(db, trip.id, operation.day_id)
    activities = _get_day_activities(db, trip_day.id)

    target_order = operation.order or len(activities) + 1
    if target_order > len(activities) + 1:
        raise ItineraryValidationError(
            "order is outside the target day"
        )

    activity_input = operation.activity
    _validate_activity_times(
        activity_input.start_time,
        activity_input.end_time,
    )
    verified_place = _verify_place(
        name=activity_input.name,
        location=activity_input.location,
        destination=trip.destination,
    )

    new_activity = models.Activity(
        trip_day_id=trip_day.id,
        name=activity_input.name,
        location=activity_input.location,
        start_time=activity_input.start_time,
        end_time=activity_input.end_time,
        estimated_cost=activity_input.estimated_cost,
        description=activity_input.description,
        order=-(len(activities) + 1),
    )
    _set_verified_place(new_activity, verified_place)

    db.add(new_activity)
    db.flush()
    activities.insert(target_order - 1, new_activity)
    _renumber_day(db, trip_day.id, activities)

    return {
        "type": operation.type,
        "activity_id": new_activity.id,
        "day_id": trip_day.id,
        "order": new_activity.order,
    }


def _update_activity(
    db: Session,
    trip: models.Trip,
    operation: UpdateActivityOperation,
) -> dict:
    activity = _get_activity(db, trip.id, operation.activity_id)
    changes = operation.changes.model_dump(exclude_unset=True)

    final_start_time = changes.get("start_time", activity.start_time)
    final_end_time = changes.get("end_time", activity.end_time)
    _validate_activity_times(final_start_time, final_end_time)

    if "name" in changes or "location" in changes:
        final_name = changes.get("name", activity.name)
        final_location = changes.get("location", activity.location)
        verified_place = _verify_place(
            name=final_name,
            location=final_location or trip.destination,
            destination=trip.destination,
        )
        _set_verified_place(activity, verified_place)

    for field_name, value in changes.items():
        setattr(activity, field_name, value)

    db.flush()

    return {
        "type": operation.type,
        "activity_id": activity.id,
    }


def _remove_activity(
    db: Session,
    trip: models.Trip,
    operation: RemoveActivityOperation,
) -> dict:
    activity = _get_activity(db, trip.id, operation.activity_id)
    day_id = activity.trip_day_id

    activity.order = -1_000_000 - activity.id
    db.flush()
    db.delete(activity)
    db.flush()

    _renumber_day(
        db,
        day_id,
        _get_day_activities(db, day_id),
    )

    return {
        "type": operation.type,
        "activity_id": operation.activity_id,
        "day_id": day_id,
    }


def _move_activity(
    db: Session,
    trip: models.Trip,
    operation: MoveActivityOperation,
) -> dict:
    activity = _get_activity(db, trip.id, operation.activity_id)
    target_day = _get_trip_day(db, trip.id, operation.target_day_id)
    source_day_id = activity.trip_day_id

    if source_day_id == target_day.id:
        activities = _get_day_activities(db, source_day_id)
        activities.remove(activity)

        if operation.target_order > len(activities) + 1:
            raise ItineraryValidationError(
                "target_order is outside the target day"
            )

        activities.insert(operation.target_order - 1, activity)
        _renumber_day(db, source_day_id, activities)
    else:
        source_activities = _get_day_activities(db, source_day_id)
        target_activities = _get_day_activities(db, target_day.id)
        source_activities.remove(activity)

        if operation.target_order > len(target_activities) + 1:
            raise ItineraryValidationError(
                "target_order is outside the target day"
            )

        for index, source_activity in enumerate(
            source_activities + [activity],
            start=1,
        ):
            source_activity.order = -(1_000 + index)

        for index, target_activity in enumerate(
            target_activities,
            start=1,
        ):
            target_activity.order = -(2_000 + index)

        db.flush()

        activity.trip_day_id = target_day.id
        activity.order = -3_000
        db.flush()

        target_activities.insert(operation.target_order - 1, activity)

        for index, source_activity in enumerate(
            source_activities,
            start=1,
        ):
            source_activity.order = index

        for index, target_activity in enumerate(
            target_activities,
            start=1,
        ):
            target_activity.order = index

        db.flush()

    return {
        "type": operation.type,
        "activity_id": activity.id,
        "source_day_id": source_day_id,
        "target_day_id": target_day.id,
        "target_order": activity.order,
    }


# === 行程编辑器：顺序执行操作，由路由统一提交事务 ===
# 流程：操作列表 → 四种处理器 → flush 校验 → 执行结果
def apply_itinerary_operations(
    db: Session,
    trip: models.Trip,
    operations: list[ItineraryOperation],
) -> list[dict]:
    results = []

    for operation in operations:
        if isinstance(operation, AddActivityOperation):
            result = _add_activity(db, trip, operation)
        elif isinstance(operation, UpdateActivityOperation):
            result = _update_activity(db, trip, operation)
        elif isinstance(operation, RemoveActivityOperation):
            result = _remove_activity(db, trip, operation)
        elif isinstance(operation, MoveActivityOperation):
            result = _move_activity(db, trip, operation)
        else:
            raise ItineraryValidationError(
                "Unsupported itinerary operation"
            )

        results.append(result)

    return results
