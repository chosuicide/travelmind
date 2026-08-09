"""add persisted trip route legs

Revision ID: e4b7c1d9a260
Revises: d7a3f1c8b2e4
Create Date: 2026-08-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e4b7c1d9a260"
down_revision: Union[str, Sequence[str], None] = "d7a3f1c8b2e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# === 行程路线段迁移：只新增路线表，不改写已有行程数据 ===
# 流程：创建 trip_legs → 连接 Day/Activity → 添加顺序与交通方式约束
def upgrade() -> None:
    op.create_table(
        "trip_legs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trip_day_id", sa.Integer(), nullable=False),
        sa.Column("origin_activity_id", sa.Integer(), nullable=False),
        sa.Column("destination_activity_id", sa.Integer(), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("distance_meters", sa.Integer(), nullable=False),
        sa.Column("duration_minutes", sa.Float(), nullable=True),
        sa.Column("estimated_cost", sa.Float(), nullable=True),
        sa.Column("walking_distance_meters", sa.Integer(), nullable=True),
        sa.Column("polyline", sa.JSON(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.CheckConstraint(
            "mode IN ('walking', 'driving', 'transit')",
            name="ck_trip_leg_mode",
        ),
        sa.CheckConstraint(
            "origin_activity_id != destination_activity_id",
            name="ck_trip_leg_distinct_activities",
        ),
        sa.ForeignKeyConstraint(
            ["trip_day_id"],
            ["trip_days.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["origin_activity_id"],
            ["activities.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["destination_activity_id"],
            ["activities.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "trip_day_id",
            "order",
            name="uq_trip_leg_order",
        ),
    )
    op.create_index(
        op.f("ix_trip_legs_id"),
        "trip_legs",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_trip_legs_trip_day_id"),
        "trip_legs",
        ["trip_day_id"],
        unique=False,
    )


# === 回滚路线段表：删除路线缓存，不影响 Trip/Day/Activity ===
# 流程：删除索引 → 删除 trip_legs → 保留原行程数据
def downgrade() -> None:
    op.drop_index(
        op.f("ix_trip_legs_trip_day_id"),
        table_name="trip_legs",
    )
    op.drop_index(
        op.f("ix_trip_legs_id"),
        table_name="trip_legs",
    )
    op.drop_table("trip_legs")
