import sqlite3
from functools import lru_cache

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver

from app.core.config import LANGGRAPH_CHECKPOINT_PATH


# === LangGraph 本地检查点：每个 GenerationRun 使用独立 thread_id ===
# 流程：运行目录 → SQLite 连接 → JsonPlus 序列化 → 图节点持久化
@lru_cache(maxsize=1)
def get_planning_checkpointer() -> SqliteSaver:
    LANGGRAPH_CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        LANGGRAPH_CHECKPOINT_PATH,
        check_same_thread=False,
    )
    return SqliteSaver(
        connection,
        serde=JsonPlusSerializer(
            pickle_fallback=True,
            allowed_msgpack_modules=[
                ("app.agent.context", "PlanningContext"),
            ],
        ),
    )
