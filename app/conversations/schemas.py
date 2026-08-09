from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from app.generation.policy import MAX_TRIP_DAYS
from app.conversations.regions import validate_region


Interest = Annotated[str, Field(min_length=1, max_length=50)]
DraftField = Literal[
    "province_code",
    "province_name",
    "city_code",
    "city_name",
    "start_date",
    "end_date",
    "duration_days",
    "budget",
    "budget_flexible",
    "people",
    "interests",
    "pace",
    "notes",
]


# === 对话草稿：所有字段在收集阶段可缺失，已有字段仍必须合法 ===
# 流程：消息提取结果 → 局部字段校验 → 合并旧草稿 → 跨字段校验
class TripDraftPatch(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    province_code: str | None = Field(default=None, min_length=6, max_length=6)
    province_name: str | None = Field(default=None, min_length=2, max_length=30)
    city_code: str | None = Field(default=None, min_length=6, max_length=6)
    city_name: str | None = Field(default=None, min_length=2, max_length=30)
    start_date: date | None = None
    end_date: date | None = None
    duration_days: int | None = Field(
        default=None,
        ge=1,
        le=MAX_TRIP_DAYS,
    )
    budget: float | None = Field(default=None, gt=0, le=1_000_000)
    budget_flexible: bool | None = None
    people: int | None = Field(default=None, ge=1, le=20)
    interests: list[Interest] | None = Field(default=None, min_length=1, max_length=10)
    pace: Literal["relaxed", "balanced", "intensive"] | None = None
    notes: str | None = Field(default=None, max_length=1000)


class TripDraft(TripDraftPatch):
    @model_validator(mode="after")
    def validate_known_rules(self):
        validate_region(
            self.province_code,
            self.province_name,
            self.city_code,
            self.city_name,
        )
        if self.start_date is not None and self.end_date is not None:
            if self.end_date < self.start_date:
                raise ValueError("end_date must not be earlier than start_date")
            total_days = (self.end_date - self.start_date).days + 1
            if total_days > MAX_TRIP_DAYS:
                raise ValueError(
                    f"trip cannot be longer than {MAX_TRIP_DAYS} days"
                )
        return self


class ConversationCreate(BaseModel):
    """创建 Agent 会话前，由受控省市选择器提供的目的地。"""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    province_code: str = Field(min_length=6, max_length=6)
    province_name: str = Field(min_length=2, max_length=30)
    city_code: str = Field(min_length=6, max_length=6)
    city_name: str = Field(min_length=2, max_length=30)

    @model_validator(mode="after")
    def validate_destination(self):
        validate_region(
            self.province_code,
            self.province_name,
            self.city_code,
            self.city_name,
        )
        return self


class ExtractedMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal["update_draft", "off_topic", "help"]
    patch: TripDraftPatch = Field(default_factory=TripDraftPatch)
    clear_fields: list[DraftField] = Field(default_factory=list)
    add_interests: list[Interest] = Field(default_factory=list, max_length=10)
    remove_interests: list[Interest] = Field(default_factory=list, max_length=10)
    assistant_message: str = Field(min_length=1, max_length=1200)
    prepare_preview: bool = False
    use_defaults: bool = False
    start_generation: bool = False
    _usage: dict = PrivateAttr(default_factory=dict)


class MessageCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    client_message_id: str = Field(min_length=1, max_length=64)
    content: str = Field(min_length=1, max_length=1000)
    draft_patch: TripDraftPatch | None = None
    clear_fields: list[DraftField] = Field(default_factory=list)
    add_interests: list[Interest] = Field(default_factory=list, max_length=10)
    remove_interests: list[Interest] = Field(default_factory=list, max_length=10)


class ChatMessageResponse(BaseModel):
    id: int
    role: str
    message_type: str
    content: str
    payload: dict
    generation_run_id: int | None
    modification_proposal_id: int | None
    created_at: datetime


class ConversationResponse(BaseModel):
    id: int
    trip_id: int | None
    status: str
    pending_proposal_id: int | None
    draft: dict
    draft_revision: int
    missing_fields: list[str]
    messages: list[ChatMessageResponse]
    created_at: datetime
    updated_at: datetime


class MessageResult(BaseModel):
    accepted: bool
    duplicate: bool
    conversation: ConversationResponse


class ConfirmationResponse(BaseModel):
    conversation_id: int
    trip_id: int
    run_id: int
    status: str


class ConversationListItem(BaseModel):
    id: int
    trip_id: int | None
    status: str
    destination: str | None
    last_message: str | None
    pending_proposal_id: int | None
    updated_at: datetime


class ConversationListResponse(BaseModel):
    items: list[ConversationListItem]
    limit: int
    offset: int
