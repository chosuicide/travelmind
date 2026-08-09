"""create the initial TravelMind schema

Revision ID: 1f6a2d4c8b90
Revises:
Create Date: 2026-08-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "1f6a2d4c8b90"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# === 初始数据库基线：让全新环境可以只依靠 Alembic 建库 ===
# 流程：用户/行程 → 行程日/活动 → 生成用量 → 建立查询索引
def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_id", "users", ["id"], unique=False)
    op.create_index(
        "ix_users_username",
        "users",
        ["username"],
        unique=True,
    )
    op.create_index(
        "ix_users_email",
        "users",
        ["email"],
        unique=True,
    )

    op.create_table(
        "trips",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("destination", sa.String(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("budget", sa.Float(), nullable=False),
        sa.Column("people", sa.Integer(), nullable=False),
        sa.Column("interests", sa.JSON(), nullable=False),
        sa.Column("pace", sa.String(), nullable=False),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_trips_id", "trips", ["id"], unique=False)

    op.create_table(
        "trip_days",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trip_id", sa.Integer(), nullable=False),
        sa.Column("day_number", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("summary", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["trip_id"],
            ["trips.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_trip_days_id",
        "trip_days",
        ["id"],
        unique=False,
    )
    op.create_index(
        "ix_trip_days_trip_id",
        "trip_days",
        ["trip_id"],
        unique=False,
    )

    op.create_table(
        "activities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trip_day_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("location", sa.String(), nullable=True),
        sa.Column("start_time", sa.String(), nullable=True),
        sa.Column("end_time", sa.String(), nullable=True),
        sa.Column("estimated_cost", sa.Float(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["trip_day_id"],
            ["trip_days.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_activities_id",
        "activities",
        ["id"],
        unique=False,
    )
    op.create_index(
        "ix_activities_trip_day_id",
        "activities",
        ["trip_day_id"],
        unique=False,
    )

    op.create_table(
        "generation_usage",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("trip_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["trip_id"],
            ["trips.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_generation_usage_id",
        "generation_usage",
        ["id"],
        unique=False,
    )
    op.create_index(
        "ix_generation_usage_user_id",
        "generation_usage",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_generation_usage_trip_id",
        "generation_usage",
        ["trip_id"],
        unique=False,
    )


# === 回滚初始数据库基线 ===
# 流程：按外键依赖逆序删除索引与表 → 回到空数据库
def downgrade() -> None:
    op.drop_index(
        "ix_generation_usage_trip_id",
        table_name="generation_usage",
    )
    op.drop_index(
        "ix_generation_usage_user_id",
        table_name="generation_usage",
    )
    op.drop_index(
        "ix_generation_usage_id",
        table_name="generation_usage",
    )
    op.drop_table("generation_usage")

    op.drop_index(
        "ix_activities_trip_day_id",
        table_name="activities",
    )
    op.drop_index("ix_activities_id", table_name="activities")
    op.drop_table("activities")

    op.drop_index("ix_trip_days_trip_id", table_name="trip_days")
    op.drop_index("ix_trip_days_id", table_name="trip_days")
    op.drop_table("trip_days")

    op.drop_index("ix_trips_id", table_name="trips")
    op.drop_table("trips")

    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_index("ix_users_id", table_name="users")
    op.drop_table("users")
