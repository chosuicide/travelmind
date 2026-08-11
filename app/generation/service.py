import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from sqlalchemy.orm import Session

from app.agent.prompts import AGENT_PROMPT_VERSION
from app.core.config import DEEPSEEK_MODEL
from app.db import models
from app.db.session import SessionLocal
from app.integrations.deepseek import generate_itinerary_with_tools
from app.itinerary.routes import rebuild_trip_routes


logger = logging.getLogger(__name__)
MAX_TRACE_EVENTS = 100


@dataclass(frozen=True)
class TripGenerationInput:
    id: int
    destination: str
    start_date: date
    end_date: date
    budget: float
    people: int
    interests: list
    pace: str
    notes: str | None


@dataclass
class GenerationTelemetry:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    tool_call_count: int = 0
    trace: list[dict] = field(default_factory=list)
    on_change: Callable[["GenerationTelemetry"], None] | None = field(
        default=None,
        repr=False,
    )

    def _notify(self) -> None:
        if self.on_change is not None:
            self.on_change(self)

    def _append(self, event: dict) -> None:
        if len(self.trace) < MAX_TRACE_EVENTS:
            self.trace.append(event)

    def record_model_usage(self, usage: dict) -> None:
        input_tokens = int(usage.get("input_tokens", 0))
        output_tokens = int(usage.get("output_tokens", 0))
        total_tokens = int(usage.get("total_tokens", 0))
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.total_tokens += total_tokens or (
            input_tokens + output_tokens
        )
        self._notify()

    def record_tool(self, payload: dict) -> None:
        self.tool_call_count += 1
        arguments = payload.get("arguments") or {}
        tool_name = payload.get("tool_name", "unknown")
        safe_arguments = {}
        if tool_name == "search_places":
            safe_arguments = {
                key: arguments.get(key)
                for key in ("keywords", "district", "category", "limit")
            }
        elif tool_name == "get_place_details":
            safe_arguments = {
                "place_provider_id": arguments.get("place_provider_id")
            }
        elif tool_name == "estimate_route":
            safe_arguments = {
                key: arguments.get(key)
                for key in (
                    "origin_place_id",
                    "destination_place_id",
                    "mode",
                )
            }
        event = {
            "type": "tool",
            "name": tool_name,
            "status": payload.get("status", "succeeded"),
            "arguments": safe_arguments,
            "candidate_count": payload.get("candidate_count", 0),
        }
        if payload.get("error"):
            event["error"] = str(payload["error"])[:300]
        tool_budget = payload.get("tool_budget")
        if isinstance(tool_budget, dict):
            remaining_by_tool = tool_budget.get("remaining_by_tool") or {}
            event["tool_budget"] = {
                "remaining_total": max(
                    int(tool_budget.get("remaining_total", 0)),
                    0,
                ),
                "remaining_by_tool": {
                    tool_name: max(
                        int(remaining_by_tool.get(tool_name, 0)),
                        0,
                    )
                    for tool_name in (
                        "search_places",
                        "get_place_details",
                        "estimate_route",
                    )
                },
            }
        self._append(event)
        self._notify()

    def record_quality(self, payload: dict) -> None:
        raw_issues = payload.get("issues")
        if raw_issues is None:
            raw_issues = payload.get("remaining_warnings", [])
        issues = []
        for issue in raw_issues[:10]:
            if isinstance(issue, dict):
                issues.append(
                    {
                        "code": str(issue.get("code", "quality"))[:100],
                        "severity": str(
                            issue.get("severity", "warning")
                        )[:20],
                        "penalty": float(issue.get("penalty", 0) or 0),
                        "message": str(issue.get("message", ""))[:500],
                    }
                )
            else:
                issues.append(
                    {
                        "code": "legacy_quality",
                        "severity": "warning",
                        "penalty": 0.0,
                        "message": str(issue)[:500],
                    }
                )
        event = {
            "type": "quality",
            "stage": payload.get("stage"),
            "issues": issues,
        }
        for key in (
            "selected",
            "warning_penalty",
            "hard_issue_count",
            "original_penalty",
            "repaired_penalty",
        ):
            if payload.get(key) is not None:
                event[key] = payload[key]
        self._append(event)
        self._notify()

    def record_graph(self, payload: dict) -> None:
        self._append(
            {
                "type": "graph",
                "node": payload.get("node"),
                "status": payload.get("status", "running"),
                "turn": int(payload.get("turn", 0) or 0),
                "next": payload.get("next"),
            }
        )
        self._notify()


# === 模块：生成过程检查点 ===
# 流程：Agent 模型/工具事件 → 更新 GenerationRun → 前端轮询读取真实进度
def persist_generation_checkpoint(
    run_id: int,
    telemetry: GenerationTelemetry,
) -> None:
    try:
        with SessionLocal() as db:
            run = db.get(models.GenerationRun, run_id)
            if run is None or run.status != "running":
                return
            run.input_tokens = telemetry.input_tokens
            run.output_tokens = telemetry.output_tokens
            run.total_tokens = telemetry.total_tokens
            run.tool_call_count = telemetry.tool_call_count
            run.trace = list(telemetry.trace)
            db.commit()
    except Exception as exc:
        logger.warning(
            "Could not persist generation checkpoint for run %s: %s",
            run_id,
            type(exc).__name__,
        )


def create_generation_run(
    db: Session,
    trip: models.Trip,
    user_id: int,
) -> models.GenerationRun:
    run = models.GenerationRun(
        trip_id=trip.id,
        user_id=user_id,
        status="queued",
        model_name=DEEPSEEK_MODEL,
        prompt_version=AGENT_PROMPT_VERSION,
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        tool_call_count=0,
        trace=[],
        created_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()
    return run


def claim_trip_for_generation(db: Session, trip_id: int) -> bool:
    updated_rows = (
        db.query(models.Trip)
        .filter(
            models.Trip.id == trip_id,
            models.Trip.status.notin_(("generating", "generated")),
        )
        .update(
            {models.Trip.status: "generating"},
            synchronize_session=False,
        )
    )
    return updated_rows == 1


def get_latest_generation_run(
    db: Session,
    trip_id: int,
) -> models.GenerationRun | None:
    return (
        db.query(models.GenerationRun)
        .filter(models.GenerationRun.trip_id == trip_id)
        .order_by(models.GenerationRun.id.desc())
        .first()
    )


def _start_run(run_id: int) -> TripGenerationInput | None:
    with SessionLocal() as db:
        started_at = datetime.now(timezone.utc)
        updated_rows = (
            db.query(models.GenerationRun)
            .filter(
                models.GenerationRun.id == run_id,
                models.GenerationRun.status == "queued",
            )
            .update(
                {
                    models.GenerationRun.status: "running",
                    models.GenerationRun.started_at: started_at,
                    models.GenerationRun.finished_at: None,
                    models.GenerationRun.error_code: None,
                    models.GenerationRun.error_message: None,
                },
                synchronize_session=False,
            )
        )
        db.commit()

        if updated_rows != 1:
            run = db.get(models.GenerationRun, run_id)
            if run is None:
                raise ValueError(f"Generation run not found: {run_id}")
            logger.info(
                "Ignoring generation run %s because its status is %s",
                run_id,
                run.status,
            )
            return None

        run = db.get(models.GenerationRun, run_id)
        trip = db.get(models.Trip, run.trip_id)
        if trip is None:
            raise ValueError(f"Trip not found for generation run: {run_id}")

        trip_input = TripGenerationInput(
            id=trip.id,
            destination=trip.destination,
            start_date=trip.start_date,
            end_date=trip.end_date,
            budget=float(trip.budget),
            people=trip.people,
            interests=list(trip.interests),
            pace=trip.pace,
            notes=trip.notes,
        )
        return trip_input


def _save_itinerary(
    db: Session,
    trip: models.Trip,
    itinerary: dict,
) -> None:
    existing_day = (
        db.query(models.TripDay)
        .filter(models.TripDay.trip_id == trip.id)
        .first()
    )
    if existing_day is not None:
        raise ValueError("Trip already contains itinerary days")

    for day_data in itinerary["days"]:
        trip_day = models.TripDay(
            trip_id=trip.id,
            day_number=day_data["day_number"],
            date=(
                trip.start_date
                + timedelta(days=day_data["day_number"] - 1)
            ),
            summary=day_data["summary"],
        )
        db.add(trip_day)
        db.flush()

        for index, activity_data in enumerate(
            day_data["activities"],
            start=1,
        ):
            verified_place = activity_data.get("verified_place")
            if verified_place is None:
                raise ValueError("Activity is missing verified place data")
            db.add(
                models.Activity(
                    trip_day_id=trip_day.id,
                    name=activity_data["name"],
                    location=activity_data["location"],
                    start_time=activity_data["start_time"],
                    end_time=activity_data["end_time"],
                    estimated_cost=activity_data["estimated_cost"],
                    description=activity_data["description"],
                    order=index,
                    place_provider="amap",
                    place_provider_id=verified_place.get("amap_id"),
                    verified_name=verified_place.get("name"),
                    verified_address=verified_place.get("address"),
                    latitude=verified_place.get("latitude"),
                    longitude=verified_place.get("longitude"),
                )
            )


def finish_linked_conversation(
    db: Session,
    run: models.GenerationRun,
    *,
    succeeded: bool,
) -> None:
    conversation = (
        db.query(models.Conversation)
        .filter(models.Conversation.trip_id == run.trip_id)
        .first()
    )
    if conversation is None:
        return
    conversation.status = "generated" if succeeded else "failed"
    conversation.updated_at = datetime.now(timezone.utc)
    db.add(
        models.ChatMessage(
            conversation_id=conversation.id,
            client_message_id=None,
            role="assistant",
            message_type="itinerary" if succeeded else "error",
            content=(
                "行程已经生成完成，可以查看每天的安排和路线。"
                if succeeded
                else "这次生成没有完成，你可以检查需求后重新尝试。"
            ),
            payload={
                "trip_id": run.trip_id,
                "run_status": "succeeded" if succeeded else "failed",
            },
            generation_run_id=run.id,
            created_at=datetime.now(timezone.utc),
        )
    )


def _complete_run(
    run_id: int,
    itinerary: dict,
    telemetry: GenerationTelemetry,
) -> None:
    with SessionLocal() as db:
        run = db.get(models.GenerationRun, run_id)
        if run is None:
            raise ValueError(f"Generation run not found: {run_id}")
        if run.status != "running":
            raise ValueError(
                f"Generation run is not running: {run.status}"
            )
        trip = db.get(models.Trip, run.trip_id)
        if trip is None:
            raise ValueError(f"Trip not found for generation run: {run_id}")

        _save_itinerary(db, trip, itinerary)
        rebuild_trip_routes(db, trip)
        trip.status = "generated"
        run.status = "succeeded"
        run.input_tokens = telemetry.input_tokens
        run.output_tokens = telemetry.output_tokens
        run.total_tokens = telemetry.total_tokens
        run.tool_call_count = telemetry.tool_call_count
        run.trace = telemetry.trace
        run.finished_at = datetime.now(timezone.utc)
        finish_linked_conversation(db, run, succeeded=True)
        db.commit()


def _fail_run(
    run_id: int,
    exc: Exception,
    telemetry: GenerationTelemetry,
) -> None:
    with SessionLocal() as db:
        run = db.get(models.GenerationRun, run_id)
        if run is None:
            logger.error("Generation run %s disappeared after failure", run_id)
            return
        if run.status == "succeeded":
            logger.error(
                "Refusing to overwrite succeeded generation run %s",
                run_id,
            )
            return
        trip = db.get(models.Trip, run.trip_id)
        if trip is not None and trip.status == "generating":
            trip.status = "generation_failed"
        run.status = "failed"
        run.input_tokens = telemetry.input_tokens
        run.output_tokens = telemetry.output_tokens
        run.total_tokens = telemetry.total_tokens
        run.tool_call_count = telemetry.tool_call_count
        run.trace = telemetry.trace
        run.error_code = type(exc).__name__[:100]
        run.error_message = (
            (str(exc)[:500] or "Generation validation failed")
            if isinstance(exc, ValueError)
            else "Generation failed; inspect server logs for details"
        )
        run.finished_at = datetime.now(timezone.utc)
        finish_linked_conversation(db, run, succeeded=False)
        db.commit()


# === 后台生成任务：网络调用与数据库事务分离，成功或失败都落任务状态 ===
# 流程：running+Trip快照 → 关闭事务 → Agent → 短事务保存 / 独立失败记录
def run_generation_task(run_id: int) -> bool:
    telemetry = GenerationTelemetry()
    telemetry.on_change = lambda snapshot: persist_generation_checkpoint(
        run_id,
        snapshot,
    )
    try:
        trip_input = _start_run(run_id)
        if trip_input is None:
            return False
        itinerary = generate_itinerary_with_tools(
            trip_input,
            on_tool_result=telemetry.record_tool,
            on_quality_result=telemetry.record_quality,
            on_model_usage=telemetry.record_model_usage,
            on_graph_event=telemetry.record_graph,
            graph_thread_id=f"generation-{run_id}",
        )
        _complete_run(run_id, itinerary, telemetry)
        return True
    except Exception as exc:
        logger.error(
            "Generation run %s failed with %s",
            run_id,
            type(exc).__name__,
        )
        _fail_run(run_id, exc, telemetry)
        return True
