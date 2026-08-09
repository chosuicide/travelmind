"""add verified place fields

Revision ID: 5292c9d007f6
Revises: 1f6a2d4c8b90
Create Date: 2026-08-07 18:40:36.557787

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5292c9d007f6'
down_revision: Union[str, Sequence[str], None] = "1f6a2d4c8b90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# === 修复 Activity Schema ===
# 流程：
# 检查现有列
# → 缺失的列才添加
# → 检查 UNIQUE
# → SQLite Batch Mode 重建表并添加约束

def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing_columns = {
        column["name"]
        for column in inspector.get_columns("activities")
    }

    # 只有不存在才添加，兼容上次执行到一半的数据库
    if "place_provider" not in existing_columns:
        op.add_column(
            "activities",
            sa.Column("place_provider", sa.String(), nullable=True),
        )

    if "place_provider_id" not in existing_columns:
        op.add_column(
            "activities",
            sa.Column("place_provider_id", sa.String(), nullable=True),
        )

    if "verified_name" not in existing_columns:
        op.add_column(
            "activities",
            sa.Column("verified_name", sa.String(), nullable=True),
        )

    if "verified_address" not in existing_columns:
        op.add_column(
            "activities",
            sa.Column("verified_address", sa.String(), nullable=True),
        )

    if "latitude" not in existing_columns:
        op.add_column(
            "activities",
            sa.Column("latitude", sa.Float(), nullable=True),
        )

    if "longitude" not in existing_columns:
        op.add_column(
            "activities",
            sa.Column("longitude", sa.Float(), nullable=True),
        )

    # 重新读取数据库约束
    inspector = sa.inspect(bind)

    unique_constraints = inspector.get_unique_constraints(
        "activities"
    )

    has_activity_order_unique = any(
        set(constraint.get("column_names") or [])
        == {"trip_day_id", "order"}
        for constraint in unique_constraints
    )

    # SQLite 无法直接 ADD CONSTRAINT
    # Alembic Batch Mode 会重建表完成这个操作
    if not has_activity_order_unique:
        with op.batch_alter_table(
            "activities",
            schema=None,
        ) as batch_op:
            batch_op.create_unique_constraint(
                "uq_activity_order",
                ["trip_day_id", "order"],
            )


# === 回滚 Activity Schema ===
# 流程：删除 UNIQUE → 删除地点字段 → 恢复旧结构

def downgrade():
    with op.batch_alter_table(
        "activities",
        schema=None,
    ) as batch_op:
        batch_op.drop_constraint(
            "uq_activity_order",
            type_="unique",
        )

        batch_op.drop_column("longitude")
        batch_op.drop_column("latitude")
        batch_op.drop_column("verified_address")
        batch_op.drop_column("verified_name")
        batch_op.drop_column("place_provider_id")
        batch_op.drop_column("place_provider")
