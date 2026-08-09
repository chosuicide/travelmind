import hashlib
import json
from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.db import models
from app.itinerary.editor import apply_itinerary_operations
from app.itinerary.schemas import (
    AddActivityOperation,
    ItineraryOperationsRequest,
    MoveActivityOperation,
    RemoveActivityOperation,
    UpdateActivityOperation,
)


class ModificationProposalError(Exception):
    """AI 修改提案错误的基类。"""


class ModificationProposalNotFound(ModificationProposalError):
    """提案不存在，或不属于当前用户和行程。"""


class ModificationProposalConflict(ModificationProposalError):
    """提案状态或行程版本发生冲突。"""


class ModificationProposalInvalid(ModificationProposalError):
    """AI 提案引用非法资源或违反业务规则。"""


# === 行程快照：生成稳定指纹，防止旧提案覆盖新修改 ===
# 流程：Trip → Days → Activities → 稳定 JSON → SHA-256
def build_itinerary_snapshot(
    db: Session,
    trip: models.Trip,
) -> dict:
    trip_days = (
        db.query(models.TripDay)
        .filter(models.TripDay.trip_id == trip.id)
        .order_by(models.TripDay.day_number)
        .all()
    )
    days = []

    for trip_day in trip_days:
        activities = (
            db.query(models.Activity)
            .filter(models.Activity.trip_day_id == trip_day.id)
            .order_by(models.Activity.order)
            .all()
        )
        days.append(
            {
                "id": trip_day.id,
                "day_number": trip_day.day_number,
                "date": trip_day.date.isoformat(),
                "summary": trip_day.summary,
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
                        "place_provider": activity.place_provider,
                        "place_provider_id": activity.place_provider_id,
                        "verified_name": activity.verified_name,
                        "verified_address": activity.verified_address,
                        "latitude": activity.latitude,
                        "longitude": activity.longitude,
                    }
                    for activity in activities
                ],
            }
        )

    return {
        "trip_id": trip.id,
        "destination": trip.destination,
        "start_date": trip.start_date.isoformat(),
        "end_date": trip.end_date.isoformat(),
        "days": days,
    }


def calculate_itinerary_hash(snapshot: dict) -> str:
    snapshot_json = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(
        snapshot_json.encode("utf-8")
    ).hexdigest()


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
        raise ModificationProposalInvalid(
            "AI proposal references an invalid trip day"
        )
    return trip_day


def _get_activity(
    db: Session,
    trip_id: int,
    activity_id: int,
) -> tuple[models.Activity, models.TripDay]:
    row = (
        db.query(models.Activity, models.TripDay)
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
    if row is None:
        raise ModificationProposalInvalid(
            "AI proposal references an invalid activity"
        )
    return row


def _activity_values(activity: models.Activity) -> dict:
    return {
        "name": activity.name,
        "location": activity.location,
        "start_time": activity.start_time,
        "end_time": activity.end_time,
        "estimated_cost": activity.estimated_cost,
        "description": activity.description,
    }


def _day_activity_count(db: Session, day_id: int) -> int:
    return db.query(models.Activity).filter(
        models.Activity.trip_day_id == day_id
    ).count()


def _validate_time_order(
    start_time: str | None,
    end_time: str | None,
):
    if start_time is not None and end_time is not None:
        if start_time >= end_time:
            raise ModificationProposalInvalid(
                "AI proposal has an invalid activity time range"
            )


# === 提案预览：把机器操作转换成字段级 before/after ===
# 流程：结构化操作 → 验证归属 → 差异列表 → 前端预览卡片
def build_operation_preview(
    db: Session,
    trip: models.Trip,
    request: ItineraryOperationsRequest,
) -> list[dict]:
    preview_items = []

    for operation in request.operations:
        if isinstance(operation, AddActivityOperation):
            trip_day = _get_trip_day(db, trip.id, operation.day_id)
            activity_count = _day_activity_count(db, trip_day.id)
            target_order = operation.order or activity_count + 1
            if target_order > activity_count + 1:
                raise ModificationProposalInvalid(
                    "AI proposal uses an invalid activity order"
                )

            activity_data = operation.activity.model_dump(mode="json")
            _validate_time_order(
                activity_data["start_time"],
                activity_data["end_time"],
            )
            preview_items.append(
                {
                    "type": operation.type,
                    "day_id": trip_day.id,
                    "day_number": trip_day.day_number,
                    "order": target_order,
                    "before": None,
                    "after": activity_data,
                }
            )
        elif isinstance(operation, UpdateActivityOperation):
            activity, trip_day = _get_activity(
                db,
                trip.id,
                operation.activity_id,
            )
            current_values = _activity_values(activity)
            changes = operation.changes.model_dump(exclude_unset=True)
            final_values = current_values | changes
            _validate_time_order(
                final_values["start_time"],
                final_values["end_time"],
            )
            preview_items.append(
                {
                    "type": operation.type,
                    "activity_id": activity.id,
                    "activity_name": activity.name,
                    "day_id": trip_day.id,
                    "day_number": trip_day.day_number,
                    "changes": [
                        {
                            "field": field_name,
                            "before": current_values[field_name],
                            "after": value,
                        }
                        for field_name, value in changes.items()
                    ],
                }
            )
        elif isinstance(operation, RemoveActivityOperation):
            activity, trip_day = _get_activity(
                db,
                trip.id,
                operation.activity_id,
            )
            preview_items.append(
                {
                    "type": operation.type,
                    "activity_id": activity.id,
                    "day_id": trip_day.id,
                    "day_number": trip_day.day_number,
                    "order": activity.order,
                    "before": _activity_values(activity),
                    "after": None,
                }
            )
        elif isinstance(operation, MoveActivityOperation):
            activity, source_day = _get_activity(
                db,
                trip.id,
                operation.activity_id,
            )
            target_day = _get_trip_day(
                db,
                trip.id,
                operation.target_day_id,
            )
            target_count = _day_activity_count(db, target_day.id)
            if source_day.id == target_day.id:
                target_count -= 1
            if operation.target_order > target_count + 1:
                raise ModificationProposalInvalid(
                    "AI proposal uses an invalid target order"
                )

            preview_items.append(
                {
                    "type": operation.type,
                    "activity_id": activity.id,
                    "activity_name": activity.name,
                    "before": {
                        "day_id": source_day.id,
                        "day_number": source_day.day_number,
                        "order": activity.order,
                    },
                    "after": {
                        "day_id": target_day.id,
                        "day_number": target_day.day_number,
                        "order": operation.target_order,
                    },
                }
            )

    return preview_items


# === 创建提案：保存操作和基准指纹，不修改 Activity ===
def create_modification_proposal(
    db: Session,
    trip: models.Trip,
    user_id: int,
    message: str,
    request: ItineraryOperationsRequest,
) -> models.ModificationProposal:
    snapshot = build_itinerary_snapshot(db, trip)
    proposal = models.ModificationProposal(
        trip_id=trip.id,
        user_id=user_id,
        request_message=message,
        operations=request.model_dump(
            mode="json",
            exclude_unset=True,
        )["operations"],
        preview=build_operation_preview(db, trip, request),
        base_itinerary_hash=calculate_itinerary_hash(snapshot),
        status="pending",
        created_at=datetime.now(timezone.utc),
    )
    db.add(proposal)
    db.flush()
    return proposal


def get_pending_modification_proposal(
    db: Session,
    trip_id: int,
    user_id: int,
) -> models.ModificationProposal | None:
    return (
        db.query(models.ModificationProposal)
        .filter(
            models.ModificationProposal.trip_id == trip_id,
            models.ModificationProposal.user_id == user_id,
            models.ModificationProposal.status == "pending",
        )
        .order_by(models.ModificationProposal.id.desc())
        .first()
    )


def _get_owned_proposal(
    db: Session,
    trip_id: int,
    user_id: int,
    proposal_id: int,
) -> models.ModificationProposal:
    proposal = (
        db.query(models.ModificationProposal)
        .filter(
            models.ModificationProposal.id == proposal_id,
            models.ModificationProposal.trip_id == trip_id,
            models.ModificationProposal.user_id == user_id,
        )
        .with_for_update()
        .first()
    )
    if proposal is None:
        raise ModificationProposalNotFound(
            "Modification proposal not found"
        )
    return proposal


# === 应用提案：重验状态和指纹，再复用统一编辑器 ===
# 流程：锁定提案 → pending → 指纹 → JSON 重验 → 编辑器 → applied
def apply_modification_proposal(
    db: Session,
    trip: models.Trip,
    user_id: int,
    proposal_id: int,
) -> tuple[models.ModificationProposal, list[dict]]:
    proposal = _get_owned_proposal(
        db,
        trip.id,
        user_id,
        proposal_id,
    )
    if proposal.status != "pending":
        raise ModificationProposalConflict(
            f"Modification proposal is already {proposal.status}"
        )

    current_hash = calculate_itinerary_hash(
        build_itinerary_snapshot(db, trip)
    )
    if current_hash != proposal.base_itinerary_hash:
        raise ModificationProposalConflict(
            "Itinerary changed after this proposal was created"
        )

    try:
        request = ItineraryOperationsRequest.model_validate(
            {"operations": proposal.operations}
        )
    except ValidationError as exc:
        raise ModificationProposalInvalid(
            "Stored modification proposal is invalid"
        ) from exc

    applied_operations = apply_itinerary_operations(
        db=db,
        trip=trip,
        operations=request.operations,
    )
    proposal.status = "applied"
    proposal.applied_at = datetime.now(timezone.utc)
    db.flush()

    return proposal, applied_operations


# === 拒绝提案：只改变 Proposal 状态，不触碰行程 ===
def dismiss_modification_proposal(
    db: Session,
    trip_id: int,
    user_id: int,
    proposal_id: int,
) -> models.ModificationProposal:
    proposal = _get_owned_proposal(
        db,
        trip_id,
        user_id,
        proposal_id,
    )
    if proposal.status != "pending":
        raise ModificationProposalConflict(
            f"Modification proposal is already {proposal.status}"
        )

    proposal.status = "dismissed"
    proposal.dismissed_at = datetime.now(timezone.utc)
    db.flush()
    return proposal


def serialize_modification_proposal(
    proposal: models.ModificationProposal,
) -> dict:
    return {
        "id": proposal.id,
        "trip_id": proposal.trip_id,
        "message": proposal.request_message,
        "status": proposal.status,
        "operations": proposal.operations,
        "preview": proposal.preview,
        "created_at": proposal.created_at,
        "applied_at": proposal.applied_at,
        "dismissed_at": proposal.dismissed_at,
    }
