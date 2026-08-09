import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from app.conversations.normalizer import normalize_patch
from app.conversations.schemas import DraftField, Interest, TripDraft, TripDraftPatch
from app.conversations.state import complete_preview_defaults, get_missing_fields, merge_draft
from app.db import models
from app.itinerary.routes import rebuild_trip_routes
from app.modifications.service import (
    ModificationProposalError,
    apply_modification_proposal,
    build_itinerary_snapshot,
    create_modification_proposal,
    dismiss_modification_proposal,
    get_pending_modification_proposal,
)


class UpdateTripContextArguments(BaseModel):
    """Save facts the user stated or changed in the current trip context."""

    model_config = ConfigDict(extra="forbid")
    patch: TripDraftPatch = Field(default_factory=TripDraftPatch)
    clear_fields: list[DraftField] = Field(default_factory=list)
    add_interests: list[Interest] = Field(default_factory=list, max_length=10)
    remove_interests: list[Interest] = Field(default_factory=list, max_length=10)


class CreateTripPreviewArguments(BaseModel):
    """Create a non-blocking review card from the current trip context."""

    model_config = ConfigDict(extra="forbid")
    use_defaults: bool = Field(
        default=True,
        description="Fill unresolved optional choices with labelled AI defaults.",
    )


class GenerateItineraryArguments(BaseModel):
    """Generate only after the user clearly confirms the current preview."""

    model_config = ConfigDict(extra="forbid")


class ProposeItineraryModificationArguments(BaseModel):
    """Turn an actionable natural-language request into a safe proposal."""

    model_config = ConfigDict(extra="forbid")
    request: str = Field(min_length=1, max_length=2000)


class ReplyToGeneratedTripArguments(BaseModel):
    """Answer or clarify without creating a modification proposal."""

    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=2000)


class ApplyItineraryModificationArguments(BaseModel):
    """Apply the current proposal after explicit user confirmation."""

    model_config = ConfigDict(extra="forbid")


class DismissItineraryModificationArguments(BaseModel):
    """Dismiss the current proposal without changing the itinerary."""

    model_config = ConfigDict(extra="forbid")


def _tool_schema(name: str, description: str, arguments: type[BaseModel]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": arguments.model_json_schema(),
        },
    }


AGENT_TOOL_SCHEMAS = [
    _tool_schema(
        "update_trip_context",
        "Persist travel facts the user stated, corrected or removed. "
        "One call may update several facts. Do not use it for questions.",
        UpdateTripContextArguments,
    ),
    _tool_schema(
        "create_trip_preview",
        "Create a reviewable, non-blocking trip requirement preview. "
        "The user remains free to keep changing requirements afterwards.",
        CreateTripPreviewArguments,
    ),
    _tool_schema(
        "generate_itinerary",
        "Accept the latest pending preview and queue itinerary generation, "
        "but only after the user clearly confirms that preview.",
        GenerateItineraryArguments,
    ),
]

GENERATED_TRIP_TOOL_SCHEMAS = [
    _tool_schema(
        "propose_itinerary_modification",
        "Create a reviewable modification proposal when the user asks to "
        "change an already generated itinerary. Never apply it immediately.",
        ProposeItineraryModificationArguments,
    ),
    _tool_schema(
        "reply_to_generated_trip",
        "Reply only when the user is asking a question or when one focused "
        "clarification is still required. Never use this to promise, submit "
        "or describe a ready modification plan; use "
        "propose_itinerary_modification for that.",
        ReplyToGeneratedTripArguments,
    ),
]


def tool_schemas_for(*, generated_trip: bool) -> list[dict]:
    return (
        GENERATED_TRIP_TOOL_SCHEMAS
        if generated_trip
        else AGENT_TOOL_SCHEMAS
    )


def _proposal_payload(proposal: models.ModificationProposal) -> dict:
    return {
        "proposal_id": proposal.id,
        "status": proposal.status,
        "operations": proposal.operations,
        "preview": proposal.preview,
    }


def build_preview_rows(draft: dict, assumed_fields: list[str]) -> list[dict]:
    assumed = set(assumed_fields)
    province = draft.get("province_name") or ""
    city = draft.get("city_name") or ""
    start_date = draft.get("start_date")
    end_date = draft.get("end_date")
    budget = draft.get("budget")
    return [
        {
            "field": "destination",
            "label": "目的地",
            "value": f"{province}{city}" or "待补充",
        },
        {
            "field": "dates",
            "label": "日期",
            "value": (
                f"{start_date} 至 {end_date}"
                if start_date and end_date
                else start_date or end_date or "待补充"
            ),
            "assumed": bool({"start_date", "end_date"} & assumed),
        },
        {
            "field": "people",
            "label": "人数",
            "value": f"{draft.get('people')} 人" if draft.get("people") else "待补充",
            "assumed": "people" in assumed,
        },
        {
            "field": "budget",
            "label": "预算",
            "value": (
                "灵活安排"
                if draft.get("budget_flexible") is True
                else f"¥{budget:g}" if budget is not None else "待补充"
            ),
            "assumed": "budget" in assumed,
        },
        {
            "field": "interests",
            "label": "偏好",
            "value": "、".join(draft.get("interests") or []) or "待补充",
            "assumed": "interests" in assumed,
        },
        {
            "field": "pace",
            "label": "节奏",
            "value": {
                "relaxed": "轻松",
                "balanced": "适中",
                "intensive": "紧凑",
            }.get(draft.get("pace"), "待补充"),
            "assumed": "pace" in assumed,
        },
    ]


def pending_preview_message(
    db: Session,
    conversation_id: int,
) -> models.ChatMessage | None:
    messages = (
        db.query(models.ChatMessage)
        .filter(
            models.ChatMessage.conversation_id == conversation_id,
            models.ChatMessage.message_type == "requirements",
        )
        .order_by(models.ChatMessage.id.desc())
        .all()
    )
    return next(
        (
            message
            for message in messages
            if (message.payload or {}).get("kind") == "draft_preview"
            and (message.payload or {}).get("status") == "pending"
        ),
        None,
    )


def stale_pending_previews(
    db: Session,
    conversation_id: int,
    *,
    reason: str,
) -> list[int]:
    stale_ids = []
    messages = (
        db.query(models.ChatMessage)
        .filter(
            models.ChatMessage.conversation_id == conversation_id,
            models.ChatMessage.message_type == "requirements",
        )
        .all()
    )
    for message in messages:
        payload = dict(message.payload or {})
        if payload.get("kind") != "draft_preview" or payload.get("status") != "pending":
            continue
        message.payload = {
            **payload,
            "status": "stale",
            "stale_reason": reason,
        }
        stale_ids.append(message.id)
    return stale_ids


@dataclass
class AgentToolContext:
    db: Session
    conversation: models.Conversation
    history: list[dict] = field(default_factory=list)
    modification_generator: Callable | None = None
    accepted: bool = False
    preview_payload: dict | None = None
    generation_preview_id: int | None = None
    proposal_payload: dict | None = None
    proposal_id: int | None = None
    trip_changed: bool = False
    response_content: str | None = None
    tool_events: list[dict] = field(default_factory=list)
    _results_by_call_id: dict[str, dict] = field(default_factory=dict)

    def execute(self, name: str, arguments: dict, tool_call_id: str) -> dict:
        if tool_call_id in self._results_by_call_id:
            return self._results_by_call_id[tool_call_id]
        try:
            if name == "update_trip_context":
                result = self._update_trip_context(arguments)
            elif name == "create_trip_preview":
                result = self._create_trip_preview(arguments)
            elif name == "generate_itinerary":
                result = self._request_generation(arguments)
            elif name == "propose_itinerary_modification":
                result = self._propose_itinerary_modification(arguments)
            elif name == "reply_to_generated_trip":
                result = self._reply_to_generated_trip(arguments)
            elif name == "apply_itinerary_modification":
                result = self._apply_itinerary_modification(arguments)
            elif name == "dismiss_itinerary_modification":
                result = self._dismiss_itinerary_modification(arguments)
            else:
                result = {"ok": False, "error": f"unknown tool: {name}"}
        except (
            ModificationProposalError,
            ValidationError,
            ValueError,
            TypeError,
        ) as exc:
            result = {"ok": False, "error": str(exc)[:500]}
        if result.get("assistant_message"):
            self.response_content = str(result["assistant_message"])
        self._results_by_call_id[tool_call_id] = result
        self.tool_events.append({"name": name, "result": result})
        return result

    def _update_trip_context(self, raw: dict) -> dict:
        arguments = UpdateTripContextArguments.model_validate(raw)
        normalized_patch, clear_fields = normalize_patch(
            self.conversation.draft,
            arguments.patch,
            arguments.clear_fields,
        )
        before = dict(self.conversation.draft)
        merged = merge_draft(
            before,
            normalized_patch,
            clear_fields=clear_fields,
            add_interests=arguments.add_interests,
            remove_interests=arguments.remove_interests,
        )
        after = merged.model_dump(mode="json", exclude_none=True)
        changed = before != after
        if changed:
            stale_pending_previews(
                self.db,
                self.conversation.id,
                reason="trip_context_updated",
            )
            self.preview_payload = None
            self.conversation.draft = after
            self.conversation.draft_revision += 1
            self.conversation.status = "collecting"
            self.accepted = True
        return {
            "ok": True,
            "changed": changed,
            "draft_revision": self.conversation.draft_revision,
            "saved_context": self.conversation.draft,
            "missing_fields": get_missing_fields(self.conversation.draft),
        }

    def _create_trip_preview(self, raw: dict) -> dict:
        arguments = CreateTripPreviewArguments.model_validate(raw)
        current = TripDraft.model_validate(self.conversation.draft)
        if arguments.use_defaults:
            candidate, assumed_fields = complete_preview_defaults(current)
        else:
            candidate, assumed_fields = current, []
        missing = get_missing_fields(candidate)
        if missing:
            return {
                "ok": False,
                "error": "preview still lacks required context",
                "missing_fields": missing,
            }
        candidate_data = candidate.model_dump(mode="json", exclude_none=True)
        self.preview_payload = {
            "kind": "draft_preview",
            "status": "pending",
            "base_revision": self.conversation.draft_revision,
            "candidate_draft": candidate_data,
            "assumed_fields": assumed_fields,
            "preview": build_preview_rows(candidate_data, assumed_fields),
            "missing_fields": [],
            "start_after_apply": True,
        }
        self.accepted = True
        return {
            "ok": True,
            "draft_revision": self.conversation.draft_revision,
            "preview": self.preview_payload["preview"],
            "assumed_fields": assumed_fields,
            "instruction": "Ask the user to review it; they may confirm or keep editing.",
        }

    def _request_generation(self, raw: dict) -> dict:
        GenerateItineraryArguments.model_validate(raw)
        if self.preview_payload is not None:
            return {
                "ok": False,
                "error": "the preview was created in this same turn; wait for user confirmation",
            }
        preview = pending_preview_message(self.db, self.conversation.id)
        if preview is None:
            return {
                "ok": False,
                "error": "there is no current preview to confirm; create one first",
            }
        payload = dict(preview.payload or {})
        if payload.get("base_revision") != self.conversation.draft_revision:
            preview.payload = {**payload, "status": "stale", "stale_reason": "revision_mismatch"}
            return {
                "ok": False,
                "error": "the preview is stale; create a new preview first",
            }
        self.generation_preview_id = preview.id
        self.accepted = True
        return {
            "ok": True,
            "preview_id": preview.id,
            "action": "generation_approved",
        }

    def _generated_trip(self) -> models.Trip:
        if self.conversation.trip_id is None:
            raise ValueError("conversation is not linked to a trip")
        trip = self.db.get(models.Trip, self.conversation.trip_id)
        if (
            trip is None
            or trip.user_id != self.conversation.user_id
            or trip.status != "generated"
        ):
            raise ValueError("generated trip not found")
        return trip

    def _mark_proposal_messages(
        self,
        proposal_id: int,
        *,
        status: str,
        stale_reason: str | None = None,
    ) -> None:
        messages = (
            self.db.query(models.ChatMessage)
            .filter(
                models.ChatMessage.conversation_id == self.conversation.id,
                models.ChatMessage.modification_proposal_id == proposal_id,
            )
            .all()
        )
        for message in messages:
            message.payload = {
                **(message.payload or {}),
                "status": status,
                **(
                    {"stale_reason": stale_reason}
                    if stale_reason is not None
                    else {}
                ),
            }

    def _propose_itinerary_modification(self, raw: dict) -> dict:
        arguments = ProposeItineraryModificationArguments.model_validate(raw)
        trip = self._generated_trip()
        pending = get_pending_modification_proposal(
            self.db,
            trip.id,
            self.conversation.user_id,
        )
        if pending is not None:
            pending.status = "dismissed"
            pending.dismissed_at = datetime.now(timezone.utc)
            self._mark_proposal_messages(
                pending.id,
                status="stale",
                stale_reason="new_modification_request",
            )

        generator = self.modification_generator
        if generator is None:
            from app.integrations.deepseek import generate_modification_response

            generator = generate_modification_response
        response = generator(
            trip=trip,
            itinerary_snapshot=build_itinerary_snapshot(self.db, trip),
            message=arguments.request,
            conversation_context=self.history,
        )
        if response.action == "clarify":
            return {
                "ok": True,
                "action": "clarify",
                "assistant_message": response.assistant_message,
            }

        proposal = create_modification_proposal(
            db=self.db,
            trip=trip,
            user_id=self.conversation.user_id,
            message=arguments.request,
            request=response.to_operations_request(),
        )
        self.proposal_payload = _proposal_payload(proposal)
        self.proposal_id = proposal.id
        self.accepted = True
        return {
            "ok": True,
            "action": "proposal_created",
            "assistant_message": response.assistant_message,
            "proposal": self.proposal_payload,
        }

    def _reply_to_generated_trip(self, raw: dict) -> dict:
        arguments = ReplyToGeneratedTripArguments.model_validate(raw)
        return {
            "ok": True,
            "action": "reply",
            "assistant_message": arguments.message,
        }

    def _apply_itinerary_modification(self, raw: dict) -> dict:
        ApplyItineraryModificationArguments.model_validate(raw)
        trip = self._generated_trip()
        pending = get_pending_modification_proposal(
            self.db,
            trip.id,
            self.conversation.user_id,
        )
        if pending is None:
            return {"ok": False, "error": "there is no pending proposal"}
        proposal, applied_operations = apply_modification_proposal(
            db=self.db,
            trip=trip,
            user_id=self.conversation.user_id,
            proposal_id=pending.id,
        )
        rebuild_trip_routes(self.db, trip)
        self._mark_proposal_messages(proposal.id, status="applied")
        self.proposal_payload = _proposal_payload(proposal)
        self.proposal_id = proposal.id
        self.trip_changed = True
        self.accepted = True
        return {
            "ok": True,
            "action": "proposal_applied",
            "proposal": self.proposal_payload,
            "applied_operation_count": len(applied_operations),
        }

    def _dismiss_itinerary_modification(self, raw: dict) -> dict:
        DismissItineraryModificationArguments.model_validate(raw)
        trip = self._generated_trip()
        pending = get_pending_modification_proposal(
            self.db,
            trip.id,
            self.conversation.user_id,
        )
        if pending is None:
            return {"ok": False, "error": "there is no pending proposal"}
        proposal = dismiss_modification_proposal(
            db=self.db,
            trip_id=trip.id,
            user_id=self.conversation.user_id,
            proposal_id=pending.id,
        )
        self._mark_proposal_messages(proposal.id, status="dismissed")
        self.proposal_payload = _proposal_payload(proposal)
        self.proposal_id = proposal.id
        self.accepted = True
        return {
            "ok": True,
            "action": "proposal_dismissed",
            "proposal": self.proposal_payload,
        }
