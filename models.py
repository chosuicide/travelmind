from sqlalchemy import Date, Float, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import Date, Float, ForeignKey, Integer, JSON, String
from database import Base

from datetime import datetime

from sqlalchemy import DateTime

from sqlalchemy import (
    Date,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)


#用户users表 主外键约束
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

    destination: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    start_date: Mapped[Date] = mapped_column(
        Date,
        nullable=False,
    )

    end_date: Mapped[Date] = mapped_column(
        Date,
        nullable=False,
    )

    budget: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    people: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    interests: Mapped[list] = mapped_column(
        JSON,
        nullable=False,
    )

    pace: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    notes: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String,
        default="created",
        nullable=False,
    )


    # === 行程天数：一趟 Trip 可以包含很多天 ===

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

    day_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    date: Mapped[Date] = mapped_column(
        Date,
        nullable=False,
    )

    summary: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

  # === Activity：保存 AI 活动 + 高德验证后的真实地点信息 ===
# 流程：
# DeepSeek生成活动
# → 高德验证
# → 得到真实 POI / 经纬度
# → 保存进 activities
# → 以后地图、路线计算直接使用

class Activity(Base):
    __tablename__ = "activities"

    __table_args__ = (
        UniqueConstraint(
            "trip_day_id",
            "order",
            name="uq_activity_order",
        ),
    )

    # 原来的字段保持不变
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

    description: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    order: Mapped[int] = mapped_column(Integer, nullable=False)

    # ↓↓↓ 新增：真实地点验证结果 ↓↓↓

    place_provider: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    place_provider_id: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    verified_name: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    verified_address: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    latitude: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    longitude: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

# === AI 生成记录：记录每一次真实 LLM 生成尝试 ===
# 流程：用户准备调用 AI → 写入记录 → 后续用于分钟限流和每日额度统计

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

