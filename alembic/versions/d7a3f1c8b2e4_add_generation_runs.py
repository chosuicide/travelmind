"""add generation runs

Revision ID: d7a3f1c8b2e4
Revises: c4e7a2b9d1f0
Create Date: 2026-08-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d7a3f1c8b2e4"
down_revision: Union[str, Sequence[str], None] = "c4e7a2b9d1f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# === 后台生成任务表：纯新增结构，不读取或改写旧行程数据 ===
# 流程：创建任务表 → 建立 Trip/User 外键 → 添加状态与查询索引
def upgrade() -> None:
    op.create_table(
        "generation_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trip_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("model_name", sa.String(), nullable=False),
        sa.Column("prompt_version", sa.String(), nullable=False),
        sa.Column(
            "input_tokens",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "output_tokens",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "total_tokens",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "tool_call_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("trace", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_generation_run_status",
        ),
        sa.ForeignKeyConstraint(
            ["trip_id"],
            ["trips.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_generation_runs_id"),
        "generation_runs",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_generation_runs_trip_id"),
        "generation_runs",
        ["trip_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_generation_runs_user_id"),
        "generation_runs",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_generation_runs_status"),
        "generation_runs",
        ["status"],
        unique=False,
    )


# === 回滚后台生成任务表：只删除任务日志，不影响实际行程 ===
# 流程：删除索引 → 删除 generation_runs → 保留 Trip/Day/Activity
def downgrade() -> None:
    op.drop_index(
        op.f("ix_generation_runs_status"),
        table_name="generation_runs",
    )
    op.drop_index(
        op.f("ix_generation_runs_user_id"),
        table_name="generation_runs",
    )
    op.drop_index(
        op.f("ix_generation_runs_trip_id"),
        table_name="generation_runs",
    )
    op.drop_index(
        op.f("ix_generation_runs_id"),
        table_name="generation_runs",
    )
    op.drop_table("generation_runs")
