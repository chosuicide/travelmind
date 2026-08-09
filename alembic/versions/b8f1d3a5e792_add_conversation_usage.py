"""add conversation model usage records

Revision ID: b8f1d3a5e792
Revises: a7e9c2f4d681
Create Date: 2026-08-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8f1d3a5e792"
down_revision: Union[str, Sequence[str], None] = "a7e9c2f4d681"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conversation_usage",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("model_name", sa.String(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_conversation_usage_id"), "conversation_usage", ["id"])
    op.create_index(
        op.f("ix_conversation_usage_user_id"),
        "conversation_usage",
        ["user_id"],
    )
    op.create_index(
        op.f("ix_conversation_usage_conversation_id"),
        "conversation_usage",
        ["conversation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_conversation_usage_conversation_id"),
        table_name="conversation_usage",
    )
    op.drop_index(
        op.f("ix_conversation_usage_user_id"),
        table_name="conversation_usage",
    )
    op.drop_index(
        op.f("ix_conversation_usage_id"),
        table_name="conversation_usage",
    )
    op.drop_table("conversation_usage")
