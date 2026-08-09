from collections.abc import Callable

from app.agent.tools import execute_travel_tool


ToolExecutor = Callable[[str, str, object], dict]


# === Agent 入口：配置依赖，具体循环由 LangGraph 子图驱动 ===
# 流程：PlanningAgent 配置 → planning_graph → 最终可信行程
class PlanningAgent:
    def __init__(
        self,
        client,
        model: str,
        tool_executor: ToolExecutor = execute_travel_tool,
        max_tool_calls: int = 8,
        max_model_turns: int = 10,
        thread_id: str | None = None,
        on_tool_result: Callable[[dict], None] | None = None,
        on_quality_result: Callable[[dict], None] | None = None,
        on_model_usage: Callable[[dict], None] | None = None,
        on_graph_event: Callable[[dict], None] | None = None,
    ):
        self.client = client
        self.model = model
        self.tool_executor = tool_executor
        self.max_tool_calls = max_tool_calls
        self.max_model_turns = max_model_turns
        self.thread_id = thread_id
        self.on_tool_result = on_tool_result
        self.on_quality_result = on_quality_result
        self.on_model_usage = on_model_usage
        self.on_graph_event = on_graph_event

    def run(self, trip) -> dict:
        from app.agent.planning_graph import run_planning_graph

        return run_planning_graph(self, trip)
