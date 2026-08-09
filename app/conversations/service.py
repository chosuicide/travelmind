from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.conversations.agent import run_conversation_agent
from app.conversations.agent.tools import stale_pending_previews
from app.conversations.extractor import (
    extract_message,
    generate_grounded_reply,
    sanitize_agent_reply,
)
from app.conversations.normalizer import (
    delegates_planning,
    detect_context_patch,
    detect_draft_preview_action,
    detect_interest_additions,
    detect_region_patch,
    normalize_patch,
    wants_generation,
)
from app.conversations.policy import assistant_reply
from app.conversations.schemas import (
    ConversationCreate,
    ExtractedMessage,
    MessageCreate,
    TripDraft,
    TripDraftPatch,
)
from app.conversations.state import (
    build_trip_input,
    complete_preview_defaults,
    conversation_status,
    get_missing_fields,
    merge_draft,
    validation_message,
)
from app.db import models
from app.itinerary.routes import rebuild_trip_routes
from app.modifications.service import (
    apply_modification_proposal,
    dismiss_modification_proposal,
    get_pending_modification_proposal,
    serialize_modification_proposal,
)
from app.generation.service import (
    claim_trip_for_generation,
    create_generation_run,
    get_latest_generation_run,
)
from app.trips.service import create_trip_record, serialize_trip
from app.usage.service import check_generation_quota, record_generation_usage
from app.usage.service import (
    check_conversation_quota,
    start_conversation_usage,
    update_conversation_usage,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _combine_usage(first: dict, second: dict) -> dict:
    return {
        key: int(first.get(key, 0) or 0) + int(second.get(key, 0) or 0)
        for key in ("input_tokens", "output_tokens", "total_tokens")
    }


def get_owned_conversation(
    db: Session,
    conversation_id: int,
    user_id: int,
) -> models.Conversation | None:
    return (
        db.query(models.Conversation)
        .filter(
            models.Conversation.id == conversation_id,
            models.Conversation.user_id == user_id,
        )
        .first()
    )


# === 模块：删除对话记录 ===
# 流程：所有权查询 → 删除 Conversation → 级联消息/用量 → 保留独立 Trip
def delete_conversation(
    db: Session,
    conversation: models.Conversation,
) -> None:
    db.delete(conversation)
    db.commit()


# === 历史会话列表：返回轻量摘要，不在列表接口加载全部消息 ===
# 流程：用户隔离 → updated_at 倒序 → 最近消息/pending 提案 → 分页摘要
def list_user_conversations(
    db: Session,
    user_id: int,
    limit: int,
    offset: int,
) -> list[dict]:
    conversations = (
        db.query(models.Conversation)
        .filter(models.Conversation.user_id == user_id)
        .order_by(
            models.Conversation.updated_at.desc(),
            models.Conversation.id.desc(),
        )
        .offset(offset)
        .limit(limit)
        .all()
    )
    items = []
    for conversation in conversations:
        last_message = (
            db.query(models.ChatMessage)
            .filter(
                models.ChatMessage.conversation_id == conversation.id
            )
            .order_by(models.ChatMessage.id.desc())
            .first()
        )
        pending = (
            get_pending_modification_proposal(
                db,
                conversation.trip_id,
                user_id,
            )
            if conversation.trip_id is not None
            else None
        )
        trip = (
            db.get(models.Trip, conversation.trip_id)
            if conversation.trip_id is not None
            else None
        )
        destination = (
            trip.destination
            if trip is not None
            else conversation.draft.get("city_name")
            or conversation.draft.get("province_name")
        )
        items.append(
            {
                "id": conversation.id,
                "trip_id": conversation.trip_id,
                "status": conversation.status,
                "destination": destination,
                "last_message": (
                    last_message.content if last_message is not None else None
                ),
                "pending_proposal_id": pending.id if pending else None,
                "updated_at": conversation.updated_at,
            }
        )
    return items


def add_message(
    db: Session,
    conversation_id: int,
    role: str,
    message_type: str,
    content: str,
    *,
    payload: dict | None = None,
    client_message_id: str | None = None,
    generation_run_id: int | None = None,
    modification_proposal_id: int | None = None,
) -> models.ChatMessage:
    message = models.ChatMessage(
        conversation_id=conversation_id,
        client_message_id=client_message_id,
        role=role,
        message_type=message_type,
        content=content,
        payload=payload or {},
        generation_run_id=generation_run_id,
        modification_proposal_id=modification_proposal_id,
        created_at=utc_now(),
    )
    db.add(message)
    db.flush()
    return message


# === 会话创建：先建立空草稿，不提前创建无效 Trip ===
# 流程：鉴权用户 → Conversation → 欢迎消息 → commit
def create_conversation(
    db: Session,
    user_id: int,
    destination: ConversationCreate | None = None,
) -> models.Conversation:
    now = utc_now()
    initial_draft = destination.model_dump() if destination is not None else {}
    conversation = models.Conversation(
        user_id=user_id,
        trip_id=None,
        status="collecting",
        draft=initial_draft,
        draft_revision=0,
        created_at=now,
        updated_at=now,
    )
    db.add(conversation)
    db.flush()
    welcome = (
        f"已选好{destination.city_name}。接下来想什么时候出发？"
        "人数、预算和偏好也可以一起告诉我，没想好的部分可以交给我安排。"
        if destination is not None
        else "这次想去哪里？你可以把已有想法都告诉我，剩下的也可以交给我安排。"
    )
    add_message(
        db,
        conversation.id,
        "assistant",
        "text",
        welcome,
    )
    db.commit()
    db.refresh(conversation)
    return conversation


def _structured_extraction(request: MessageCreate) -> ExtractedMessage:
    return ExtractedMessage(
        intent="update_draft",
        patch=request.draft_patch or TripDraftPatch(),
        clear_fields=request.clear_fields,
        add_interests=request.add_interests,
        remove_interests=request.remove_interests,
        assistant_message="我已更新这部分旅行需求。",
    )


def get_pending_draft_preview(
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
    for message in messages:
        payload = message.payload or {}
        if (
            payload.get("kind") == "draft_preview"
            and payload.get("status") == "pending"
        ):
            return message
    return None


def _natural_requirement_turns(db: Session, conversation_id: int) -> int:
    messages = (
        db.query(models.ChatMessage)
        .filter(
            models.ChatMessage.conversation_id == conversation_id,
            models.ChatMessage.role == "user",
        )
        .all()
    )
    return sum(
        1
        for message in messages
        if (message.payload or {}).get("source") != "structured"
        and (message.payload or {}).get("action") is None
    )


def _draft_preview_rows(draft: dict, assumed_fields: list[str]) -> list[dict]:
    assumed = set(assumed_fields)
    province = draft.get("province_name") or ""
    city = draft.get("city_name") or ""
    destination = f"{province}{city}" or "待补充"
    start_date = draft.get("start_date")
    end_date = draft.get("end_date")
    date_value = (
        f"{start_date} 至 {end_date}"
        if start_date and end_date
        else start_date or end_date or "待补充"
    )
    budget = draft.get("budget")
    budget_value = (
        "灵活安排"
        if draft.get("budget_flexible") is True
        else f"¥{budget:g}" if budget is not None else "待补充"
    )
    pace_value = {
        "relaxed": "轻松",
        "balanced": "适中",
        "intensive": "紧凑",
    }.get(draft.get("pace"), "待补充")
    return [
        {"field": "destination", "label": "目的地", "value": destination},
        {
            "field": "dates",
            "label": "日期",
            "value": date_value,
            "assumed": bool({"start_date", "end_date"} & assumed),
        },
        {
            "field": "people",
            "label": "人数",
            "value": f"{draft.get('people')} 人",
            "assumed": "people" in assumed,
        },
        {
            "field": "budget",
            "label": "预算",
            "value": budget_value,
            "assumed": "budget" in assumed,
        },
        {
            "field": "interests",
            "label": "偏好",
            "value": "、".join(draft.get("interests") or []),
            "assumed": "interests" in assumed,
        },
        {
            "field": "pace",
            "label": "节奏",
            "value": pace_value,
            "assumed": "pace" in assumed,
        },
    ]


def _get_owned_draft_preview(
    db: Session,
    conversation: models.Conversation,
    user_id: int,
    message_id: int,
) -> models.ChatMessage:
    if conversation.user_id != user_id:
        raise ValueError("conversation not found")
    message = (
        db.query(models.ChatMessage)
        .filter(
            models.ChatMessage.id == message_id,
            models.ChatMessage.conversation_id == conversation.id,
            models.ChatMessage.message_type == "requirements",
        )
        .first()
    )
    if message is None or (message.payload or {}).get("kind") != "draft_preview":
        raise ValueError("draft preview not found")
    if message.payload.get("status") != "pending":
        raise ValueError("draft preview has already been resolved")
    return message


# === 模块：需求草稿提案确认 ===
# 流程：读取 pending 预览 → 校验基础版本 → 合并完整候选 → 更新会话状态
def apply_draft_preview(
    db: Session,
    conversation: models.Conversation,
    user_id: int,
    message_id: int,
) -> models.Conversation:
    message = _get_owned_draft_preview(
        db,
        conversation,
        user_id,
        message_id,
    )
    payload = dict(message.payload)
    base_revision = payload.get("base_revision")
    if base_revision is not None:
        if base_revision != conversation.draft_revision:
            message.payload = {
                **payload,
                "status": "stale",
                "stale_reason": "revision_mismatch",
            }
            db.commit()
            raise ValueError("draft changed after this preview was created")
    elif payload.get("base_draft", {}) != conversation.draft:
        raise ValueError("draft changed after this preview was created")

    candidate = TripDraft.model_validate(payload.get("candidate_draft") or {})
    candidate_data = candidate.model_dump(mode="json", exclude_none=True)
    if candidate_data != conversation.draft:
        conversation.draft = candidate_data
        conversation.draft_revision += 1
    conversation.status = conversation_status(candidate)
    conversation.updated_at = utc_now()
    message.payload = {**payload, "status": "applied"}

    if payload.get("start_after_apply") and conversation.status == "ready_to_confirm":
        confirm_conversation(db, conversation, user_id)
    else:
        add_message(
            db,
            conversation.id,
            "assistant",
            "requirements",
            assistant_reply(
                conversation.draft,
                changed=True,
            ),
            payload={"accepted": True},
        )
        db.commit()
    db.refresh(conversation)
    return conversation


def dismiss_draft_preview(
    db: Session,
    conversation: models.Conversation,
    user_id: int,
    message_id: int,
) -> models.Conversation:
    message = _get_owned_draft_preview(
        db,
        conversation,
        user_id,
        message_id,
    )
    payload = dict(message.payload)
    base_revision = payload.get("base_revision")
    if base_revision is not None:
        if base_revision != conversation.draft_revision:
            raise ValueError("draft changed after this preview was created")
    elif payload.get("base_draft", {}) != conversation.draft:
        raise ValueError("draft changed after this preview was created")
    # 预览只是确认界面；关闭预览不能撤销已经写入的工作草稿。
    conversation.status = "collecting"
    message.payload = {**payload, "status": "dismissed"}
    conversation.updated_at = utc_now()
    add_message(
        db,
        conversation.id,
        "assistant",
        "text",
        "这份预览已关闭，已经收集的信息仍然保留，你可以继续调整。",
    )
    db.commit()
    db.refresh(conversation)
    return conversation


def process_agent_message(
    db: Session,
    conversation: models.Conversation,
    request: MessageCreate,
) -> tuple[bool, bool]:
    generated_mode = conversation.status == "generated"
    duplicate = (
        db.query(models.ChatMessage)
        .filter(
            models.ChatMessage.conversation_id == conversation.id,
            models.ChatMessage.client_message_id == request.client_message_id,
        )
        .first()
    )
    if duplicate is not None:
        return bool(duplicate.payload.get("accepted", False)), True
    if conversation.status not in (
        "collecting",
        "ready_to_confirm",
        "failed",
        "generated",
    ):
        raise ValueError("conversation no longer accepts requirement messages")

    # === 模块：待确认修改提案锁 ===
    # 流程：生成态会话 → 查询 pending 提案 → 阻止新消息 → 用户通过按钮处理
    if generated_mode and conversation.trip_id is not None:
        pending_proposal = get_pending_modification_proposal(
            db,
            conversation.trip_id,
            conversation.user_id,
        )
        if pending_proposal is not None:
            raise ValueError("请先应用或暂不修改当前行程方案")

    check_conversation_quota(conversation.user_id, db)
    usage = start_conversation_usage(
        conversation.user_id,
        conversation.id,
        db,
    )
    recent_history = [
        {"role": item.role, "content": item.content}
        for item in reversed(
            db.query(models.ChatMessage)
            .filter(models.ChatMessage.conversation_id == conversation.id)
            .order_by(models.ChatMessage.id.desc())
            .limit(30)
            .all()
        )
        if item.role in {"user", "assistant"}
    ]
    user_message = add_message(
        db,
        conversation.id,
        "user",
        "text",
        request.content,
        payload={"source": "agent"},
        client_message_id=request.client_message_id,
    )

    try:
        result = run_conversation_agent(
            db,
            conversation,
            request.content,
            recent_history,
        )
    except Exception:
        db.rollback()
        db.refresh(conversation)
        usage = start_conversation_usage(
            conversation.user_id,
            conversation.id,
            db,
        )
        add_message(
            db,
            conversation.id,
            "user",
            "text",
            request.content,
            payload={"source": "agent", "accepted": False},
            client_message_id=request.client_message_id,
        )
        add_message(
            db,
            conversation.id,
            "assistant",
            "error",
            "Agent 暂时没有完成这次处理，原有旅行信息没有被修改，请重试。",
            payload={"accepted": False},
        )
        update_conversation_usage(usage, {})
        db.commit()
        return False, False

    update_conversation_usage(usage, result.usage)
    user_message.payload = {
        "source": "agent",
        "accepted": result.accepted,
        "tool_events": result.tool_events,
    }
    conversation.updated_at = utc_now()

    if result.generation_preview_id is not None:
        apply_draft_preview(
            db,
            conversation,
            conversation.user_id,
            result.generation_preview_id,
        )
        db.refresh(conversation)
        return True, False

    assistant_payload = {"accepted": result.accepted}
    message_type = "text"
    if result.proposal_payload is not None:
        message_type = "proposal"
        assistant_payload = {
            **result.proposal_payload,
            "accepted": result.accepted,
        }
    elif result.preview_payload is not None:
        message_type = "requirements"
        stale_pending_previews(
            db,
            conversation.id,
            reason="new_preview_created",
        )
        assistant_payload = {
            **result.preview_payload,
            "accepted": True,
        }
    elif not generated_mode:
        assistant_payload = {
            **assistant_payload,
            "draft_revision": conversation.draft_revision,
            "missing_fields": get_missing_fields(conversation.draft),
        }
    conversation.status = "generated" if generated_mode else "collecting"
    add_message(
        db,
        conversation.id,
        "assistant",
        message_type,
        result.content,
        payload=assistant_payload,
        modification_proposal_id=result.proposal_id,
    )
    db.commit()
    db.refresh(conversation)
    return result.accepted, False


# === 消息处理：幂等保存、AI 提取、后端合并验证三步分离 ===
# 流程：client_message_id 去重 → AI/控件补丁 → 草稿验证 → 状态推进
def process_message(
    db: Session,
    conversation: models.Conversation,
    request: MessageCreate,
) -> tuple[bool, bool]:
    if request.draft_patch is None:
        return process_agent_message(db, conversation, request)

    duplicate = (
        db.query(models.ChatMessage)
        .filter(
            models.ChatMessage.conversation_id == conversation.id,
            models.ChatMessage.client_message_id == request.client_message_id,
        )
        .first()
    )
    if duplicate is not None:
        return bool(duplicate.payload.get("accepted", False)), True

    if conversation.status not in ("collecting", "ready_to_confirm", "failed"):
        raise ValueError("conversation no longer accepts requirement messages")

    pending_preview = get_pending_draft_preview(db, conversation.id)
    if pending_preview is not None:
        action = detect_draft_preview_action(request.content)
        if action is None:
            raise ValueError("请先确认或放弃当前需求预览")
        user_message = add_message(
            db,
            conversation.id,
            "user",
            "text",
            request.content,
            payload={
                "accepted": True,
                "source": "chat",
                "action": action,
            },
            client_message_id=request.client_message_id,
        )
        if action == "apply":
            apply_draft_preview(
                db,
                conversation,
                conversation.user_id,
                pending_preview.id,
            )
        else:
            dismiss_draft_preview(
                db,
                conversation,
                conversation.user_id,
                pending_preview.id,
            )
        return True, False

    conversation_usage = None
    if request.draft_patch is None:
        check_conversation_quota(conversation.user_id, db)
        conversation_usage = start_conversation_usage(
            conversation.user_id,
            conversation.id,
            db,
        )

    recent_history = [
        {"role": item.role, "content": item.content}
        for item in reversed(
            db.query(models.ChatMessage)
            .filter(models.ChatMessage.conversation_id == conversation.id)
            .order_by(models.ChatMessage.id.desc())
            .limit(30)
                .all()
        )
        if item.role in {"user", "assistant"}
    ]

    user_message = add_message(
        db,
        conversation.id,
        "user",
        "text",
        request.content,
        payload={
            "source": (
                "structured" if request.draft_patch is not None else "chat"
            )
        },
        client_message_id=request.client_message_id,
    )

    processing_failed = False
    try:
        extraction = (
            _structured_extraction(request)
            if request.draft_patch is not None
            else extract_message(
                conversation.draft,
                request.content,
                history=recent_history,
            )
        )
        if conversation_usage is not None:
            update_conversation_usage(
                conversation_usage,
                extraction._usage,
            )
        if request.draft_patch is None and delegates_planning(request.content):
            extraction.prepare_preview = True
            extraction.use_defaults = True
        explicit_patch = extraction.patch.model_dump(
            mode="json",
            exclude_unset=True,
            exclude_none=True,
        )
        context_patch = detect_context_patch(
            conversation.draft,
            request.content,
        )
        if context_patch is not None:
            extraction.patch = TripDraftPatch.model_validate(
                {
                    **explicit_patch,
                    **context_patch.model_dump(
                        mode="json",
                        exclude_unset=True,
                        exclude_none=True,
                    ),
                }
            )
            explicit_patch = extraction.patch.model_dump(
                mode="json",
                exclude_unset=True,
                exclude_none=True,
            )
            extraction.intent = "update_draft"
        contextual_interests = detect_interest_additions(request.content)
        if contextual_interests:
            extraction.add_interests = list(
                dict.fromkeys(
                    [*extraction.add_interests, *contextual_interests]
                )
            )
            extraction.intent = "update_draft"
        if not ({"province_code", "province_name", "city_code", "city_name"} & explicit_patch.keys()):
            verified_region = detect_region_patch(request.content)
            if verified_region is not None:
                extraction.patch = TripDraftPatch.model_validate(
                    {
                        **explicit_patch,
                        **verified_region.model_dump(
                            mode="json",
                            exclude_none=True,
                        ),
                    }
                )
                extraction.intent = "update_draft"
        accepted = False
        if extraction.intent == "update_draft":
            normalized_patch, clear_fields = normalize_patch(
                conversation.draft,
                extraction.patch,
                extraction.clear_fields,
            )
            draft = merge_draft(
                conversation.draft,
                normalized_patch,
                clear_fields=clear_fields,
                add_interests=extraction.add_interests,
                remove_interests=extraction.remove_interests,
            )
            accepted = bool(
                normalized_patch.model_dump(
                    exclude_unset=True,
                    exclude_none=True,
                )
                or clear_fields
                or extraction.add_interests
                or extraction.remove_interests
            )
            conversation.draft = draft.model_dump(mode="json", exclude_none=True)
            if request.draft_patch is None:
                conversation.status = "collecting"
            else:
                conversation.status = conversation_status(draft)
                extraction.assistant_message = assistant_reply(
                    conversation.draft,
                    changed=accepted,
                )
    except ValidationError as exc:
        processing_failed = True
        accepted = False
        extraction = ExtractedMessage(
            intent="help",
            assistant_message=(
                "这部分需求没有通过校验，请换一种说法："
                f"{validation_message(exc)}"
            ),
        )
    except (ValueError, TypeError) as exc:
        processing_failed = True
        accepted = False
        extraction = ExtractedMessage(
            intent="help",
            assistant_message=f"这部分需求暂时不能采用：{str(exc)[:300]}",
        )
    except Exception:
        processing_failed = True
        accepted = False
        extraction = ExtractedMessage(
            intent="help",
            assistant_message=(
                "需求理解服务暂时不可用，这条消息没有写入旅行草稿，请稍后重试。"
            ),
        )

    conversation.updated_at = utc_now()
    natural_turns = _natural_requirement_turns(db, conversation.id)
    start_requested = (
        extraction.start_generation
        or wants_generation(request.content)
    )
    current_draft = TripDraft.model_validate(conversation.draft)
    preview_requested = (
        extraction.prepare_preview
        or start_requested
        or (
            natural_turns >= 3
            and not get_missing_fields(current_draft)
            and accepted
        )
    )
    if (
        not processing_failed
        and request.draft_patch is None
        and extraction.intent != "off_topic"
        and preview_requested
    ):
        if extraction.use_defaults or start_requested:
            candidate, assumed_fields = complete_preview_defaults(current_draft)
        else:
            candidate = current_draft
            assumed_fields = []
        if not get_missing_fields(candidate):
            candidate_data = candidate.model_dump(
                mode="json",
                exclude_none=True,
            )
            preview_rows = _draft_preview_rows(
                candidate_data,
                assumed_fields,
            )
            try:
                extraction.assistant_message, reply_usage = generate_grounded_reply(
                    conversation.draft,
                    request.content,
                    history=recent_history,
                    accepted=accepted,
                    missing_fields=[],
                    preview={
                        "candidate_draft": candidate_data,
                        "assumed_fields": assumed_fields,
                        "rows": preview_rows,
                    },
                )
                extraction._usage = _combine_usage(
                    extraction._usage,
                    reply_usage,
                )
                if conversation_usage is not None:
                    update_conversation_usage(
                        conversation_usage,
                        extraction._usage,
                    )
            except Exception:
                extraction.assistant_message = (
                    "我已经按你的想法整理成预览，标记为 AI 默认的内容"
                    "都可以继续调整。"
                )
            user_message.payload = {
                **(user_message.payload or {}),
                "accepted": accepted,
                "pending_preview": True,
            }
            add_message(
                db,
                conversation.id,
                "assistant",
                "requirements",
                extraction.assistant_message,
                payload={
                    "kind": "draft_preview",
                    "status": "pending",
                    "base_draft": conversation.draft,
                    "candidate_draft": candidate_data,
                    "assumed_fields": assumed_fields,
                    "preview": preview_rows,
                    "missing_fields": [],
                    "start_after_apply": (
                        extraction.start_generation
                        or wants_generation(request.content)
                        or extraction.prepare_preview
                    ),
                    "natural_turns": natural_turns,
                },
            )
            conversation.status = "collecting"
            db.commit()
            db.refresh(conversation)
            return True, False

    if start_requested and not processing_failed and request.draft_patch is not None:
        if conversation.status == "ready_to_confirm":
            user_message.payload = {"accepted": True}
            confirm_conversation(db, conversation, conversation.user_id)
            db.refresh(conversation)
            return True, False
    if (
        not processing_failed
        and request.draft_patch is None
        and extraction.intent != "off_topic"
    ):
        try:
            extraction.assistant_message, reply_usage = generate_grounded_reply(
                conversation.draft,
                request.content,
                history=recent_history,
                accepted=accepted,
                missing_fields=get_missing_fields(conversation.draft),
                preview=None,
            )
            extraction._usage = _combine_usage(
                extraction._usage,
                reply_usage,
            )
            if conversation_usage is not None:
                update_conversation_usage(
                    conversation_usage,
                    extraction._usage,
                )
        except Exception:
            extraction.assistant_message = assistant_reply(
                conversation.draft,
                changed=accepted,
            )
    user_message.payload = {
        **(user_message.payload or {}),
        "accepted": accepted,
    }
    add_message(
        db,
        conversation.id,
        "assistant",
        (
            "requirements"
            if accepted
            else "error" if processing_failed else "text"
        ),
        extraction.assistant_message,
        payload={
            "accepted": accepted,
            "missing_fields": get_missing_fields(conversation.draft),
        },
    )
    db.commit()
    db.refresh(conversation)
    return accepted, False


def _proposal_payload(proposal: models.ModificationProposal) -> dict:
    return {
        "proposal_id": proposal.id,
        "status": proposal.status,
        "operations": proposal.operations,
        "preview": proposal.preview,
    }


def _sync_proposal_message_status(
    db: Session,
    proposal_id: int,
    status: str,
) -> None:
    messages = (
        db.query(models.ChatMessage)
        .filter(
            models.ChatMessage.modification_proposal_id == proposal_id,
        )
        .all()
    )
    for message in messages:
        message.payload = {**(message.payload or {}), "status": status}


# === 会话内确认提案：复用哈希冲突检查和统一行程编辑器 ===
# 流程：Conversation/Trip 归属 → pending Proposal → apply → 路线重建 → 消息回写
def apply_conversation_proposal(
    db: Session,
    conversation: models.Conversation,
    user_id: int,
    proposal_id: int,
) -> dict:
    if conversation.trip_id is None:
        raise ValueError("conversation is not linked to a trip")
    trip = db.get(models.Trip, conversation.trip_id)
    if trip is None or trip.user_id != user_id or trip.status != "generated":
        raise ValueError("generated trip not found")
    proposal, applied_operations = apply_modification_proposal(
        db=db,
        trip=trip,
        user_id=user_id,
        proposal_id=proposal_id,
    )
    _sync_proposal_message_status(db, proposal.id, "applied")
    rebuild_trip_routes(db, trip)
    add_message(
        db,
        conversation.id,
        "assistant",
        "proposal",
        "修改已经应用，行程和路线已重新计算。",
        payload=_proposal_payload(proposal),
        modification_proposal_id=proposal.id,
    )
    conversation.updated_at = utc_now()
    db.commit()
    db.refresh(proposal)
    return {
        "proposal": serialize_modification_proposal(proposal),
        "applied_operations": applied_operations,
        "trip": serialize_trip(trip, db),
    }


def dismiss_conversation_proposal(
    db: Session,
    conversation: models.Conversation,
    user_id: int,
    proposal_id: int,
) -> dict:
    if conversation.trip_id is None:
        raise ValueError("conversation is not linked to a trip")
    trip = db.get(models.Trip, conversation.trip_id)
    if trip is None or trip.user_id != user_id or trip.status != "generated":
        raise ValueError("generated trip not found")
    proposal = dismiss_modification_proposal(
        db=db,
        trip_id=trip.id,
        user_id=user_id,
        proposal_id=proposal_id,
    )
    _sync_proposal_message_status(db, proposal.id, "dismissed")
    add_message(
        db,
        conversation.id,
        "assistant",
        "proposal",
        "这份修改提案已取消，原行程保持不变。",
        payload=_proposal_payload(proposal),
        modification_proposal_id=proposal.id,
    )
    conversation.updated_at = utc_now()
    db.commit()
    db.refresh(proposal)
    return {
        "proposal": serialize_modification_proposal(proposal),
        "trip": serialize_trip(trip, db),
    }


# === 用户确认：草稿到 Trip 的唯一闸门，并复用现有生成队列 ===
# 流程：完整草稿 → Trip → claim → GenerationRun → usage → generating
def confirm_conversation(
    db: Session,
    conversation: models.Conversation,
    user_id: int,
) -> tuple[models.Trip, models.GenerationRun]:
    trip = None
    if conversation.trip_id is not None:
        trip = db.get(models.Trip, conversation.trip_id)
        run = get_latest_generation_run(db, conversation.trip_id)
        if (
            trip is not None
            and run is not None
            and run.status in ("queued", "running", "succeeded")
        ):
            return trip, run

    if conversation.status != "ready_to_confirm":
        raise ValueError("conversation requirements are not ready to confirm")

    trip_input = build_trip_input(conversation.draft)
    check_generation_quota(user_id, db)
    if trip is None:
        trip = create_trip_record(db, user_id, trip_input)
    else:
        trip.destination = trip_input.destination
        trip.start_date = trip_input.start_date
        trip.end_date = trip_input.end_date
        trip.budget = trip_input.budget
        trip.people = trip_input.people
        trip.interests = trip_input.interests
        trip.pace = trip_input.pace
        trip.notes = trip_input.notes
        trip.status = "generation_failed"
        db.flush()
    if not claim_trip_for_generation(db, trip.id):
        db.rollback()
        raise RuntimeError("trip generation state changed")
    run = create_generation_run(db, trip, user_id)
    conversation.trip_id = trip.id
    conversation.status = "generating"
    conversation.updated_at = utc_now()
    add_message(
        db,
        conversation.id,
        "assistant",
        "progress",
        "需求已确认，TravelMind Agent 已开始生成行程。",
        payload={"trip_id": trip.id, "run_status": run.status},
        generation_run_id=run.id,
    )
    record_generation_usage(user_id, trip.id, db)
    db.refresh(trip)
    db.refresh(run)
    return trip, run


def serialize_conversation(
    db: Session,
    conversation: models.Conversation,
) -> dict:
    messages = (
        db.query(models.ChatMessage)
        .filter(models.ChatMessage.conversation_id == conversation.id)
        .order_by(models.ChatMessage.id)
        .all()
    )
    pending_proposal = (
        get_pending_modification_proposal(
            db,
            conversation.trip_id,
            conversation.user_id,
        )
        if conversation.trip_id is not None
        else None
    )
    return {
        "id": conversation.id,
        "trip_id": conversation.trip_id,
        "status": conversation.status,
        "pending_proposal_id": (
            pending_proposal.id if pending_proposal is not None else None
        ),
        "draft": conversation.draft,
        "draft_revision": conversation.draft_revision,
        "missing_fields": get_missing_fields(conversation.draft),
        "messages": [
            {
                "id": message.id,
                "role": message.role,
                "message_type": message.message_type,
                "content": (
                    sanitize_agent_reply(message.content)
                    if message.role == "assistant"
                    else message.content
                ),
                "payload": message.payload,
                "generation_run_id": message.generation_run_id,
                "modification_proposal_id": (
                    message.modification_proposal_id
                ),
                "created_at": message.created_at,
            }
            for message in messages
        ],
        "created_at": conversation.created_at,
        "updated_at": conversation.updated_at,
    }
