"""allow proposal chat message type

Revision ID: a7e9c2f4d681
Revises: f5c8d2a4b791
Create Date: 2026-08-09

"""
from typing import Sequence, Union

from alembic import op


revision: str = "a7e9c2f4d681"
down_revision: Union[str, Sequence[str], None] = "f5c8d2a4b791"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# === 提案消息类型迁移：扩展聊天约束，不改写消息内容 ===
# 流程：批量重建约束 → 保留原消息 → 允许 proposal 类型
def upgrade() -> None:
    with op.batch_alter_table(
        "chat_messages",
        recreate="always",
    ) as batch_op:
        batch_op.drop_constraint(
            "ck_chat_message_type",
            type_="check",
        )
        batch_op.create_check_constraint(
            "ck_chat_message_type",
            "message_type IN ('text', 'requirements', 'progress', "
            "'itinerary', 'proposal', 'error')",
        )


def downgrade() -> None:
    op.execute(
        "UPDATE chat_messages SET message_type = 'itinerary' "
        "WHERE message_type = 'proposal'"
    )
    with op.batch_alter_table(
        "chat_messages",
        recreate="always",
    ) as batch_op:
        batch_op.drop_constraint(
            "ck_chat_message_type",
            type_="check",
        )
        batch_op.create_check_constraint(
            "ck_chat_message_type",
            "message_type IN ('text', 'requirements', 'progress', "
            "'itinerary', 'error')",
        )
