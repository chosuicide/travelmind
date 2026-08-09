"""add modification proposals

Revision ID: c4e7a2b9d1f0
Revises: 8b9f2c1d4e6a
Create Date: 2026-08-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c4e7a2b9d1f0"
down_revision: Union[str, Sequence[str], None] = "8b9f2c1d4e6a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# === AI 修改提案表：保存待确认操作，不直接改写行程 ===
# 流程：创建表 → 建立用户/行程外键 → 添加查询索引 → 约束提案状态
def upgrade() -> None:
    op.create_table(
        "modification_proposals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trip_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("request_message", sa.String(), nullable=False),
        sa.Column("operations", sa.JSON(), nullable=False),
        sa.Column("preview", sa.JSON(), nullable=False),
        sa.Column(
            "base_itinerary_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "applied_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "dismissed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'applied', 'dismissed')",
            name="ck_modification_proposal_status",
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
        op.f("ix_modification_proposals_id"),
        "modification_proposals",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_modification_proposals_trip_id"),
        "modification_proposals",
        ["trip_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_modification_proposals_user_id"),
        "modification_proposals",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_modification_proposals_status"),
        "modification_proposals",
        ["status"],
        unique=False,
    )


# === 回滚 AI 修改提案表 ===
# 流程：删除索引 → 删除提案表 → 不影响 Trips / Days / Activities
def downgrade() -> None:
    op.drop_index(
        op.f("ix_modification_proposals_status"),
        table_name="modification_proposals",
    )
    op.drop_index(
        op.f("ix_modification_proposals_user_id"),
        table_name="modification_proposals",
    )
    op.drop_index(
        op.f("ix_modification_proposals_trip_id"),
        table_name="modification_proposals",
    )
    op.drop_index(
        op.f("ix_modification_proposals_id"),
        table_name="modification_proposals",
    )
    op.drop_table("modification_proposals")
