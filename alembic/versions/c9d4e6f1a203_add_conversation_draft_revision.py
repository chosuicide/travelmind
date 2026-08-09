"""add conversation draft revision

Revision ID: c9d4e6f1a203
Revises: b8f1d3a5e792
Create Date: 2026-08-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c9d4e6f1a203"
down_revision: Union[str, Sequence[str], None] = "b8f1d3a5e792"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# === 会话草稿版本：让预览可过期，而不是锁住整个聊天 ===
# 流程：任何真实草稿变化 → revision + 1 → 旧预览标记 stale → 继续自由聊天
def upgrade() -> None:
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.add_column(
            sa.Column(
                "draft_revision",
                sa.Integer(),
                server_default="0",
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.drop_column("draft_revision")
