from alembic import command
from alembic.config import Config

from app.core.config import PROJECT_ROOT


# === 数据库初始化入口：只运行版本化迁移，不使用 create_all 绕过历史 ===
# 流程：读取 alembic.ini → 使用 DATABASE_URL → upgrade head → 输出结果
def main() -> None:
    alembic_config = Config(str(PROJECT_ROOT / "alembic.ini"))
    command.upgrade(alembic_config, "head")
    print("TravelMind database is at the latest Alembic revision.")


if __name__ == "__main__":
    main()
