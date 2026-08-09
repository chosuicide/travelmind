import json
from dataclasses import dataclass
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, MessagesState, StateGraph

from app.conversations.agent.prompt import (
    build_agent_system_prompt,
    build_generated_trip_system_prompt,
)
from app.conversations.agent.tools import (
    AgentToolContext,
    pending_preview_message,
    tool_schemas_for,
)
from app.conversations.extractor import sanitize_agent_reply
from app.core.config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
from app.db import models
from app.modifications.service import (
    build_itinerary_snapshot,
    get_pending_modification_proposal,
)


@dataclass(frozen=True)
class AgentRunResult:
    content: str
    accepted: bool
    usage: dict
    preview_payload: dict | None
    generation_preview_id: int | None
    tool_events: list[dict]
    proposal_payload: dict | None = None
    proposal_id: int | None = None
    trip_changed: bool = False


def _build_model() -> ChatOpenAI:
    return ChatOpenAI(
        model=DEEPSEEK_MODEL,
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        timeout=30.0,
        max_retries=1,
        max_tokens=700,
        extra_body={"thinking": {"type": "disabled"}},
    )


def _message_content(message: AIMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    text_parts = [
        block.get("text", "")
        for block in message.content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "\n".join(part for part in text_parts if part)


def _usage(messages: list) -> dict:
    input_tokens = output_tokens = total_tokens = 0
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        usage = message.usage_metadata or {}
        input_tokens += int(usage.get("input_tokens", 0) or 0)
        output_tokens += int(usage.get("output_tokens", 0) or 0)
        total_tokens += int(usage.get("total_tokens", 0) or 0)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens or input_tokens + output_tokens,
    }


def _should_force_modification_proposal(
    user_message: str,
    history: list[dict] | None,
) -> bool:
    """Recognize only the commit boundary; the model still designs the change."""
    text = user_message.strip().lower()
    question_markers = ("为什么", "怎么", "如何", "是否", "吗", "？", "?")
    direct_change_markers = (
        "帮我改",
        "我想改",
        "想换",
        "换掉",
        "换成",
        "改成",
        "改一下",
        "调整一下",
        "安排得",
        "安排成",
        "删除",
        "删掉",
        "增加",
        "新增",
        "不要去",
        "不要安排",
    )
    if not any(marker in text for marker in question_markers):
        if text.startswith(("换 ", "换，", "改 ", "改，", "调整 ", "调整，")):
            return True
        if any(marker in text for marker in direct_change_markers):
            return True

    affirmative_markers = (
        "可以",
        "继续",
        "是的",
        "对",
        "行",
        "好",
        "就这样",
        "按这个",
        "开始吧",
        "提交吧",
        "生成吧",
    )
    compact = "".join(text.split()).strip("。！!，,")
    if len(compact) > 16 or not any(
        compact.startswith(marker) for marker in affirmative_markers
    ):
        return False

    last_assistant = next(
        (
            str(item.get("content", ""))
            for item in reversed(history or [])
            if item.get("role") == "assistant" and item.get("content")
        ),
        "",
    )
    plan_markers = (
        "修改",
        "调整",
        "换成",
        "方案",
        "方向",
        "提交",
        "生成",
        "这样安排",
        "对吗",
        "可以吗",
    )
    return any(marker in last_assistant for marker in plan_markers)


# === LangGraph 会话循环：Agent 自主选择工具，工具结果再返回 Agent ===
# 流程：Agent → 有工具则执行 → Agent读取结果 → 无工具则自然结束
def run_conversation_agent(
    db,
    conversation,
    user_message: str,
    history: list[dict] | None = None,
    *,
    model=None,
) -> AgentRunResult:
    generated_trip = conversation.status == "generated" and conversation.trip_id is not None
    context = AgentToolContext(
        db=db,
        conversation=conversation,
        history=list(history or []),
    )
    if generated_trip:
        trip = db.get(models.Trip, conversation.trip_id)
        if trip is None or trip.user_id != conversation.user_id:
            raise ValueError("generated trip not found")
        pending_proposal = get_pending_modification_proposal(
            db,
            trip.id,
            conversation.user_id,
        )
        proposal_summary = (
            {
                "proposal_id": pending_proposal.id,
                "status": pending_proposal.status,
                "preview": pending_proposal.preview,
            }
            if pending_proposal is not None
            else None
        )
        system_prompt = build_generated_trip_system_prompt(
            build_itinerary_snapshot(db, trip),
            proposal_summary,
        )
    else:
        current_preview = pending_preview_message(db, conversation.id)
        preview_summary = None
        if current_preview is not None:
            preview_summary = {
                "message_id": current_preview.id,
                "base_revision": (current_preview.payload or {}).get("base_revision"),
                "status": (current_preview.payload or {}).get("status"),
            }
        system_prompt = build_agent_system_prompt(
            conversation.draft,
            conversation.draft_revision,
            preview_summary,
        )
    initial_messages = [SystemMessage(content=system_prompt)]
    for item in (history or [])[-30:]:
        if item.get("role") == "user" and item.get("content"):
            initial_messages.append(HumanMessage(content=item["content"]))
        elif item.get("role") == "assistant" and item.get("content"):
            initial_messages.append(AIMessage(content=item["content"]))
    initial_messages.append(HumanMessage(content=user_message))

    base_model = model or _build_model()
    available_tools = tool_schemas_for(generated_trip=generated_trip)
    force_proposal = generated_trip and _should_force_modification_proposal(
        user_message,
        history,
    )
    if force_proposal:
        available_tools = [
            schema
            for schema in available_tools
            if schema["function"]["name"]
            == "propose_itinerary_modification"
        ]
    tool_choice = "required" if generated_trip else "auto"
    llm = base_model.bind_tools(
        available_tools,
        tool_choice=tool_choice,
        parallel_tool_calls=False,
    )

    def agent_node(state: MessagesState) -> dict:
        return {"messages": [llm.invoke(state["messages"])]}

    def tool_node(state: MessagesState) -> dict:
        last_message = state["messages"][-1]
        tool_messages = []
        for tool_call in last_message.tool_calls:
            result = context.execute(
                tool_call["name"],
                tool_call.get("args") or {},
                tool_call["id"],
            )
            tool_messages.append(
                ToolMessage(
                    content=json.dumps(result, ensure_ascii=False),
                    tool_call_id=tool_call["id"],
                    name=tool_call["name"],
                )
            )
        return {"messages": tool_messages}

    def route(state: MessagesState) -> Literal["tools", "__end__"]:
        last_message = state["messages"][-1]
        return "tools" if getattr(last_message, "tool_calls", None) else END

    def route_after_tools(state: MessagesState) -> Literal["agent", "__end__"]:
        if not generated_trip:
            return "agent"
        last_result = context.tool_events[-1]["result"]
        return END if last_result.get("ok") else "agent"

    builder = StateGraph(MessagesState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", tool_node)
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", route, ["tools", END])
    builder.add_conditional_edges(
        "tools",
        route_after_tools,
        {"agent": "agent", END: END},
    )
    graph = builder.compile()
    state = graph.invoke(
        {"messages": initial_messages},
        config={"recursion_limit": 10},
    )
    final_message = state["messages"][-1]
    content = sanitize_agent_reply(
        context.response_content or _message_content(final_message)
    )
    if not content:
        content = "我已经理解这次调整，你可以继续告诉我想法。"
    return AgentRunResult(
        content=content,
        accepted=context.accepted,
        usage=_usage(state["messages"]),
        preview_payload=context.preview_payload,
        generation_preview_id=context.generation_preview_id,
        tool_events=list(context.tool_events),
        proposal_payload=context.proposal_payload,
        proposal_id=context.proposal_id,
        trip_changed=context.trip_changed,
    )
