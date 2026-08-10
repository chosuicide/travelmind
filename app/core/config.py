import os
from pathlib import Path

from dotenv import load_dotenv


# === 应用配置：集中读取环境变量和运行参数 ===
# 流程：定位项目根目录 → 加载 .env → 导出各模块共用配置
PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"sqlite:///{(PROJECT_ROOT / 'travelmind.db').as_posix()}",
)

# === JWT 安全边界：仓库不再提供可预测的开发密钥 ===
# 流程：读取环境变量 → 拒绝缺失/示例值/短密钥 → 认证服务统一使用
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "").strip()
if (
    len(JWT_SECRET_KEY) < 32
    or JWT_SECRET_KEY
    in {
        "travelmind-dev-secret-key",
        "replace-with-a-long-random-secret",
    }
):
    raise RuntimeError(
        "JWT_SECRET_KEY must be configured with at least 32 random characters"
    )
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# 0 表示关闭额度限制。开源/本地环境默认不限，部署者可以在 .env 中开启。
MAX_GENERATIONS_PER_MINUTE = int(
    os.getenv("MAX_GENERATIONS_PER_MINUTE", "0")
)
MAX_GENERATIONS_PER_DAY = int(
    os.getenv("MAX_GENERATIONS_PER_DAY", "0")
)
MAX_CHAT_MESSAGES_PER_MINUTE = int(
    os.getenv("MAX_CHAT_MESSAGES_PER_MINUTE", "0")
)
MAX_CHAT_MESSAGES_PER_DAY = int(
    os.getenv("MAX_CHAT_MESSAGES_PER_DAY", "0")
)
MAX_GLOBAL_GENERATIONS_PER_MINUTE = int(
    os.getenv("MAX_GLOBAL_GENERATIONS_PER_MINUTE", "0")
)
MAX_GLOBAL_GENERATIONS_PER_DAY = int(
    os.getenv("MAX_GLOBAL_GENERATIONS_PER_DAY", "0")
)
MAX_GLOBAL_CHAT_MESSAGES_PER_MINUTE = int(
    os.getenv("MAX_GLOBAL_CHAT_MESSAGES_PER_MINUTE", "0")
)
MAX_GLOBAL_CHAT_MESSAGES_PER_DAY = int(
    os.getenv("MAX_GLOBAL_CHAT_MESSAGES_PER_DAY", "0")
)

# === 公开演示配置：限制注册规模，并在部署启动时准备测试账号 ===
# 流程：读取环境变量 → 未配置时保持本地开发行为 → 生产启动器按需创建账号
MAX_REGISTERED_USERS = int(os.getenv("MAX_REGISTERED_USERS", "0"))
DEMO_USER_USERNAME = os.getenv("DEMO_USER_USERNAME", "").strip()
DEMO_USER_EMAIL = os.getenv("DEMO_USER_EMAIL", "").strip()
DEMO_USER_PASSWORD = os.getenv("DEMO_USER_PASSWORD", "")

SERVE_FRONTEND = os.getenv("SERVE_FRONTEND", "0").lower() in {
    "1",
    "true",
    "yes",
}

GENERATION_WORKER_POLL_SECONDS = float(
    os.getenv("GENERATION_WORKER_POLL_SECONDS", "2")
)
GENERATION_STALE_AFTER_MINUTES = int(
    os.getenv("GENERATION_STALE_AFTER_MINUTES", "30")
)

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-v4-flash"

AMAP_API_KEY = os.getenv("AMAP_API_KEY")
AMAP_PLACE_URL = "https://restapi.amap.com/v5/place/text"
AMAP_PLACE_DETAIL_URL = "https://restapi.amap.com/v5/place/detail"
AMAP_WALKING_ROUTE_URL = "https://restapi.amap.com/v5/direction/walking"
AMAP_DRIVING_ROUTE_URL = "https://restapi.amap.com/v5/direction/driving"
AMAP_TRANSIT_ROUTE_URL = (
    "https://restapi.amap.com/v3/direction/transit/integrated"
)

_langgraph_checkpoint_path = Path(
    os.getenv("LANGGRAPH_CHECKPOINT_PATH", ".runtime/langgraph-checkpoints.db")
)
LANGGRAPH_CHECKPOINT_PATH = (
    _langgraph_checkpoint_path
    if _langgraph_checkpoint_path.is_absolute()
    else PROJECT_ROOT / _langgraph_checkpoint_path
)
