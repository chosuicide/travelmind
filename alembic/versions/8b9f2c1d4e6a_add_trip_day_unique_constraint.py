"""add trip day unique constraint

Revision ID: 8b9f2c1d4e6a
Revises: 5292c9d007f6
Create Date: 2026-08-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "8b9f2c1d4e6a"
down_revision: Union[str, Sequence[str], None] = "5292c9d007f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# === TripDay 唯一约束迁移 ===
# 流程：检查重复 Day → 有重复则停止 → SQLite Batch Mode 重建表 → 添加 UNIQUE

def upgrade() -> None:
    connection = op.get_bind()

    duplicate_rows = connection.execute(
        sa.text(
            """
            SELECT trip_id, day_number, COUNT(*) AS duplicate_count
            FROM trip_days
            GROUP BY trip_id, day_number
            HAVING COUNT(*) > 1
            ORDER BY trip_id, day_number
            """
        )
    ).mappings().all()

    if duplicate_rows:
        duplicate_details = "; ".join(
            (
                f"trip_id={row['trip_id']}, "
                f"day_number={row['day_number']}, "
                f"count={row['duplicate_count']}"
            )
            for row in duplicate_rows
        )

        raise RuntimeError(
            "Cannot add uq_trip_day_number because duplicate "
            f"TripDay rows exist: {duplicate_details}"
        )

    with op.batch_alter_table(
        "trip_days",
        schema=None,
    ) as batch_op:
        batch_op.create_unique_constraint(
            "uq_trip_day_number",
            ["trip_id", "day_number"],
        )


# === 回滚 TripDay 唯一约束 ===
# 流程：SQLite Batch Mode 重建表 → 删除 UNIQUE → 保留原有 TripDay 数据

def downgrade() -> None:
    with op.batch_alter_table(
        "trip_days",
        schema=None,
    ) as batch_op:
        batch_op.drop_constraint(
            "uq_trip_day_number",
            type_="unique",
        )
