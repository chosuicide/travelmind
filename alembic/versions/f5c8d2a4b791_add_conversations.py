"""add chat conversations and messages

Revision ID: f5c8d2a4b791
Revises: e4b7c1d9a260
Create Date: 2026-08-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f5c8d2a4b791"
down_revision: Union[str, Sequence[str], None] = "e4b7c1d9a260"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# === 对话层迁移：新增草稿与消息表，不改写任何已有 Trip 数据 ===
# 流程：conversations → chat_messages → 外键/唯一约束/查询索引
def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("trip_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("draft", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('collecting', 'ready_to_confirm', "
            "'generating', 'generated', 'failed')",
            name="ck_conversation_status",
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
        sa.UniqueConstraint("trip_id", name="uq_conversation_trip"),
    )
    op.create_index(op.f("ix_conversations_id"), "conversations", ["id"])
    op.create_index(
        op.f("ix_conversations_status"),
        "conversations",
        ["status"],
    )
    op.create_index(
        op.f("ix_conversations_trip_id"),
        "conversations",
        ["trip_id"],
    )
    op.create_index(
        op.f("ix_conversations_user_id"),
        "conversations",
        ["user_id"],
    )

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("client_message_id", sa.String(length=64), nullable=True),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("message_type", sa.String(), nullable=False),
        sa.Column("content", sa.String(length=2000), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("generation_run_id", sa.Integer(), nullable=True),
        sa.Column("modification_proposal_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'system')",
            name="ck_chat_message_role",
        ),
        sa.CheckConstraint(
            "message_type IN ('text', 'requirements', 'progress', "
            "'itinerary', 'error')",
            name="ck_chat_message_type",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["generation_run_id"],
            ["generation_runs.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["modification_proposal_id"],
            ["modification_proposals.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conversation_id",
            "client_message_id",
            name="uq_chat_message_client_id",
        ),
    )
    op.create_index(
        op.f("ix_chat_messages_id"),
        "chat_messages",
        ["id"],
    )
    op.create_index(
        op.f("ix_chat_messages_conversation_id"),
        "chat_messages",
        ["conversation_id"],
    )
    op.create_index(
        op.f("ix_chat_messages_generation_run_id"),
        "chat_messages",
        ["generation_run_id"],
    )
    op.create_index(
        op.f("ix_chat_messages_modification_proposal_id"),
        "chat_messages",
        ["modification_proposal_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_chat_messages_modification_proposal_id"),
        table_name="chat_messages",
    )
    op.drop_index(
        op.f("ix_chat_messages_generation_run_id"),
        table_name="chat_messages",
    )
    op.drop_index(
        op.f("ix_chat_messages_conversation_id"),
        table_name="chat_messages",
    )
    op.drop_index(op.f("ix_chat_messages_id"), table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index(op.f("ix_conversations_user_id"), table_name="conversations")
    op.drop_index(op.f("ix_conversations_trip_id"), table_name="conversations")
    op.drop_index(op.f("ix_conversations_status"), table_name="conversations")
    op.drop_index(op.f("ix_conversations_id"), table_name="conversations")
    op.drop_table("conversations")
