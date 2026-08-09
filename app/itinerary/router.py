from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db import models
from app.db.session import get_db
from app.generation.policy import MAX_TRIP_DAYS
from app.generation.schemas import (
    GenerationAcceptedResponse,
    GenerationRunResponse,
)
from app.generation.service import (
    claim_trip_for_generation,
    create_generation_run,
    get_latest_generation_run,
)
from app.itinerary.editor import (
    ItineraryResourceNotFound,
    ItineraryValidationError,
    PlaceVerificationUnavailable,
    apply_itinerary_operations,
)
from app.itinerary.schemas import ItineraryOperationsRequest
from app.itinerary.routes import (
    RouteCalculationUnavailable,
    rebuild_trip_routes,
)
from app.trips.service import get_owned_trip, serialize_trip
from app.usage.service import (
    check_generation_quota,
    record_generation_usage,
)


router = APIRouter(prefix="/trips", tags=["itinerary"])


# === AI 行程生成入队：API 只持久化任务，不在 Web 进程执行 Agent ===
# 流程：权限/额度 → queued + generating → 记录用量 → 202 → Worker 领取
@router.post(
    "/{trip_id}/generate",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=GenerationAcceptedResponse,
)
def generate_trip(
    trip_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    trip = get_owned_trip(db, trip_id, current_user.id)
    if trip is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Trip not found",
        )

    total_days = (trip.end_date - trip.start_date).days + 1
    if total_days < 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Trip end_date cannot be earlier than start_date",
        )
    if total_days > MAX_TRIP_DAYS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"TravelMind supports trips of up to "
                f"{MAX_TRIP_DAYS} days"
            ),
        )

    if trip.status == "generating":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Trip is already generating",
        )

    if trip.status == "generated":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Trip already generated",
        )

    check_generation_quota(current_user.id, db)
    if not claim_trip_for_generation(db, trip.id):
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Trip generation state changed; please refresh",
        )
    run = create_generation_run(db, trip, current_user.id)
    record_generation_usage(current_user.id, trip.id, db)

    return {
        "trip_id": trip.id,
        "run_id": run.id,
        "status": run.status,
    }


# === 最新生成任务：前端轮询任务状态，成功后再读取完整 Trip ===
# 流程：权限 → 最新 run → 状态/Token/有限轨迹 → 响应
@router.get(
    "/{trip_id}/generation-runs/latest",
    response_model=GenerationRunResponse,
)
def read_latest_generation_run(
    trip_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    trip = get_owned_trip(db, trip_id, current_user.id)
    if trip is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Trip not found",
        )

    run = get_latest_generation_run(db, trip.id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Generation run not found",
        )
    return run


# === 手动编辑行程：四种结构化操作共用一个事务入口 ===
# 流程：权限 → 操作校验 → 地点验证/排序 → COMMIT → 完整行程
@router.post("/{trip_id}/itinerary/operations")
def edit_itinerary(
    trip_id: int,
    request: ItineraryOperationsRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    trip = get_owned_trip(db, trip_id, current_user.id)
    if trip is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Trip not found",
        )

    if trip.status != "generated":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Trip has not been generated yet",
        )

    try:
        applied_operations = apply_itinerary_operations(
            db=db,
            trip=trip,
            operations=request.operations,
        )
        rebuild_trip_routes(db, trip)
        db.commit()
    except ItineraryResourceNotFound as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ItineraryValidationError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    except PlaceVerificationUnavailable as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    except RouteCalculationUnavailable as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        db.rollback()
        print(f"Itinerary editing failed: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to edit itinerary",
        ) from exc

    db.refresh(trip)

    return {
        "applied_operations": applied_operations,
        "trip": serialize_trip(trip, db),
    }
