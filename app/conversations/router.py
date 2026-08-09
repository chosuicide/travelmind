from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.conversations.schemas import (
    ConfirmationResponse,
    ConversationCreate,
    ConversationListResponse,
    ConversationResponse,
    MessageCreate,
    MessageResult,
)
from app.conversations.service import (
    apply_conversation_proposal,
    apply_draft_preview,
    confirm_conversation,
    create_conversation,
    dismiss_conversation_proposal,
    dismiss_draft_preview,
    delete_conversation,
    get_owned_conversation,
    list_user_conversations,
    process_message,
    serialize_conversation,
)
from app.db import models
from app.db.session import get_db
from app.itinerary.editor import (
    ItineraryResourceNotFound,
    ItineraryValidationError,
    PlaceVerificationUnavailable,
)
from app.itinerary.routes import RouteCalculationUnavailable
from app.modifications.service import (
    ModificationProposalConflict,
    ModificationProposalInvalid,
    ModificationProposalNotFound,
)


router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("", response_model=ConversationListResponse)
def list_conversations(
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return {
        "items": list_user_conversations(
            db,
            current_user.id,
            limit,
            offset,
        ),
        "limit": limit,
        "offset": offset,
    }


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ConversationResponse)
def start_conversation(
    request: ConversationCreate | None = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    conversation = create_conversation(db, current_user.id, request)
    return serialize_conversation(db, conversation)


@router.get("/{conversation_id}", response_model=ConversationResponse)
def read_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    conversation = get_owned_conversation(db, conversation_id, current_user.id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return serialize_conversation(db, conversation)


@router.delete(
    "/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    conversation = get_owned_conversation(db, conversation_id, current_user.id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    delete_conversation(db, conversation)


@router.post("/{conversation_id}/messages", response_model=MessageResult)
def send_message(
    conversation_id: int,
    request: MessageCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    conversation = get_owned_conversation(db, conversation_id, current_user.id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        accepted, duplicate = process_message(db, conversation, request)
    except (ValueError, RuntimeError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "accepted": accepted,
        "duplicate": duplicate,
        "conversation": serialize_conversation(db, conversation),
    }


@router.post(
    "/{conversation_id}/confirm",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ConfirmationResponse,
)
def confirm_requirements(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    conversation = get_owned_conversation(db, conversation_id, current_user.id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        trip, run = confirm_conversation(db, conversation, current_user.id)
    except (ValueError, RuntimeError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "conversation_id": conversation.id,
        "trip_id": trip.id,
        "run_id": run.id,
        "status": run.status,
    }


@router.post(
    "/{conversation_id}/draft-previews/{message_id}/apply",
    response_model=ConversationResponse,
)
def apply_requirement_preview(
    conversation_id: int,
    message_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    conversation = get_owned_conversation(db, conversation_id, current_user.id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        apply_draft_preview(
            db,
            conversation,
            current_user.id,
            message_id,
        )
    except (ValueError, RuntimeError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return serialize_conversation(db, conversation)


@router.post(
    "/{conversation_id}/draft-previews/{message_id}/dismiss",
    response_model=ConversationResponse,
)
def dismiss_requirement_preview(
    conversation_id: int,
    message_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    conversation = get_owned_conversation(db, conversation_id, current_user.id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        dismiss_draft_preview(
            db,
            conversation,
            current_user.id,
            message_id,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return serialize_conversation(db, conversation)


@router.post(
    "/{conversation_id}/modification-proposals/{proposal_id}/apply"
)
def apply_chat_modification(
    conversation_id: int,
    proposal_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    conversation = get_owned_conversation(db, conversation_id, current_user.id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        return apply_conversation_proposal(
            db,
            conversation,
            current_user.id,
            proposal_id,
        )
    except ModificationProposalNotFound as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ModificationProposalConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ModificationProposalInvalid, ItineraryValidationError) as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ItineraryResourceNotFound as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (PlaceVerificationUnavailable, RouteCalculationUnavailable) as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/{conversation_id}/modification-proposals/{proposal_id}/dismiss"
)
def dismiss_chat_modification(
    conversation_id: int,
    proposal_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    conversation = get_owned_conversation(db, conversation_id, current_user.id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        return dismiss_conversation_proposal(
            db,
            conversation,
            current_user.id,
            proposal_id,
        )
    except ModificationProposalNotFound as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ModificationProposalConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
