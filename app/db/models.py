from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


# === 用户：保存账号和密码哈希 ===
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )
    username: Mapped[str] = mapped_column(
        String,
        nullable=False,
        unique=True,
        index=True,
    )
    email: Mapped[str] = mapped_column(
        String,
        nullable=False,
        unique=True,
        index=True,
    )
    password_hash: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )


# === Trip：保存用户提交的旅行需求和生成状态 ===
class Trip(Base):
    __tablename__ = "trips"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    destination: Mapped[str] = mapped_column(String, nullable=False)
    start_date: Mapped[Date] = mapped_column(Date, nullable=False)
    end_date: Mapped[Date] = mapped_column(Date, nullable=False)
    budget: Mapped[float] = mapped_column(Float, nullable=False)
    people: Mapped[int] = mapped_column(Integer, nullable=False)
    interests: Mapped[list] = mapped_column(JSON, nullable=False)
    pace: Mapped[str] = mapped_column(String, nullable=False)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(
        String,
        default="created",
        nullable=False,
    )


# === TripDay：一个 Trip 中按 day_number 唯一的旅行日 ===
class TripDay(Base):
    __tablename__ = "trip_days"

    __table_args__ = (
        UniqueConstraint(
            "trip_id",
            "day_number",
            name="uq_trip_day_number",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )
    trip_id: Mapped[int] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    day_number: Mapped[int] = mapped_column(Integer, nullable=False)
    date: Mapped[Date] = mapped_column(Date, nullable=False)
    summary: Mapped[str | None] = mapped_column(String, nullable=True)


# === Activity：保存活动内容和高德验证后的真实地点信息 ===
# 流程：AI/用户活动 → 高德验证 → POI 元数据 → 按 Day 和 order 保存
class Activity(Base):
    __tablename__ = "activities"

    __table_args__ = (
        UniqueConstraint(
            "trip_day_id",
            "order",
            name="uq_activity_order",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )
    trip_day_id: Mapped[int] = mapped_column(
        ForeignKey("trip_days.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    location: Mapped[str | None] = mapped_column(String, nullable=True)
    start_time: Mapped[str | None] = mapped_column(String, nullable=True)
    end_time: Mapped[str | None] = mapped_column(String, nullable=True)
    estimated_cost: Mapped[float] = mapped_column(
        Float,
        default=0,
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    order: Mapped[int] = mapped_column(Integer, nullable=False)
    place_provider: Mapped[str | None] = mapped_column(String, nullable=True)
    place_provider_id: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )
    verified_name: Mapped[str | None] = mapped_column(String, nullable=True)
    verified_address: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)


# === 路线段：保存同一天两个相邻活动之间的真实高德路线 ===
# 流程：相邻 Activity → 高德算路 → 距离/耗时/轨迹 → 前端地图绘制
class TripLeg(Base):
    __tablename__ = "trip_legs"

    __table_args__ = (
        UniqueConstraint(
            "trip_day_id",
            "order",
            name="uq_trip_leg_order",
        ),
        CheckConstraint(
            "mode IN ('walking', 'driving', 'transit')",
            name="ck_trip_leg_mode",
        ),
        CheckConstraint(
            "origin_activity_id != destination_activity_id",
            name="ck_trip_leg_distinct_activities",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )
    trip_day_id: Mapped[int] = mapped_column(
        ForeignKey("trip_days.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    origin_activity_id: Mapped[int] = mapped_column(
        ForeignKey("activities.id", ondelete="CASCADE"),
        nullable=False,
    )
    destination_activity_id: Mapped[int] = mapped_column(
        ForeignKey("activities.id", ondelete="CASCADE"),
        nullable=False,
    )
    order: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[str] = mapped_column(String, nullable=False)
    distance_meters: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_minutes: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    estimated_cost: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    walking_distance_meters: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    polyline: Mapped[list] = mapped_column(JSON, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)


# === AI 使用记录：支持分钟限流和每日额度统计 ===
class GenerationUsage(Base):
    __tablename__ = "generation_usage"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    trip_id: Mapped[int] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


# === AI 生成任务：记录后台任务状态、成本和有限运行轨迹 ===
# 流程：queued → running → succeeded / failed → 提供排错与成本数据
# === 对话模型用量：单独统计需求理解调用，避免与行程生成额度混在一起 ===
# 流程：Conversation 消息 → 额度检查 → 模型调用 → Token 回写
class ConversationUsage(Base):
    __tablename__ = "conversation_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    model_name: Mapped[str] = mapped_column(String, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class GenerationRun(Base):
    __tablename__ = "generation_runs"

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_generation_run_status",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )
    trip_id: Mapped[int] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String,
        default="queued",
        nullable=False,
        index=True,
    )
    model_name: Mapped[str] = mapped_column(String, nullable=False)
    prompt_version: Mapped[str] = mapped_column(String, nullable=False)
    input_tokens: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    output_tokens: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    total_tokens: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    tool_call_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    trace: Mapped[list] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    error_code: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


# === AI 修改提案：AI 只保存建议，用户确认后才真正修改行程 ===
# 流程：AI 操作 → pending → 用户确认或拒绝 → applied / dismissed
# === 对话会话：保存尚未确认的需求草稿，并连接最终 Trip ===
# 流程：collecting → ready_to_confirm → generating → generated / failed
class Conversation(Base):
    __tablename__ = "conversations"

    __table_args__ = (
        CheckConstraint(
            "status IN ('collecting', 'ready_to_confirm', "
            "'generating', 'generated', 'failed')",
            name="ck_conversation_status",
        ),
        UniqueConstraint("trip_id", name="uq_conversation_trip"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    trip_id: Mapped[int | None] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String,
        default="collecting",
        nullable=False,
        index=True,
    )
    draft: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    draft_revision: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


# === 对话消息：记录用户输入、Agent 回复和生成任务进度 ===
# 流程：用户消息 → 结构化草稿/拒绝原因 → 确认 → Worker 结果消息
class ChatMessage(Base):
    __tablename__ = "chat_messages"

    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'assistant', 'system')",
            name="ck_chat_message_role",
        ),
        CheckConstraint(
            "message_type IN ('text', 'requirements', 'progress', "
            "'itinerary', 'proposal', 'error')",
            name="ck_chat_message_type",
        ),
        UniqueConstraint(
            "conversation_id",
            "client_message_id",
            name="uq_chat_message_client_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    client_message_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    role: Mapped[str] = mapped_column(String, nullable=False)
    message_type: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(String(2000), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    generation_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("generation_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    modification_proposal_id: Mapped[int | None] = mapped_column(
        ForeignKey("modification_proposals.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class ModificationProposal(Base):
    __tablename__ = "modification_proposals"

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'applied', 'dismissed')",
            name="ck_modification_proposal_status",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )
    trip_id: Mapped[int] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    request_message: Mapped[str] = mapped_column(String, nullable=False)
    operations: Mapped[list] = mapped_column(JSON, nullable=False)
    preview: Mapped[list] = mapped_column(JSON, nullable=False)
    base_itinerary_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String,
        default="pending",
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    dismissed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
