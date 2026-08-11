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
from app.modifications.actions import detect_proposal_action
from app.modifications.service import (
    build_itinerary_snapshot,
    get_pending_modification_proposal,
)


ConversationPhase = Literal[
    "collecting",
    "preview_pending",
    "generated",
    "proposal_pending",
]


class ConversationGraphState(MessagesState):
    phase: ConversationPhase


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


# === LangGraph 会话状态路由：数据库状态决定阶段，模型只决定阶段内动作 ===
# 流程：读取 collecting/preview/generated/proposal → 决策动作 → 工具执行 → 回复
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
    pending_proposal = None
    current_preview = None
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
    phase: ConversationPhase
    if pending_proposal is not None:
        phase = "proposal_pending"
    elif generated_trip:
        phase = "generated"
    elif current_preview is not None:
        phase = "preview_pending"
    else:
        phase = "collecting"

    if phase == "proposal_pending":
        proposal_action = detect_proposal_action(user_message)
        if proposal_action is not None:
            tool_name = {
                "apply": "apply_itinerary_modification",
                "dismiss": "dismiss_itinerary_modification",
            }[proposal_action]
            result = context.execute(
                tool_name,
                {},
                f"deterministic-{proposal_action}-{pending_proposal.id}",
            )
            if not result.get("ok"):
                raise ValueError(result.get("error") or "proposal action failed")
            content = {
                "apply": "修改已经应用，行程与路线也已重新计算。",
                "dismiss": "这份修改建议已取消，原行程保持不变。",
            }[proposal_action]
            return AgentRunResult(
                content=content,
                accepted=context.accepted,
                usage={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                preview_payload=None,
                generation_preview_id=None,
                tool_events=list(context.tool_events),
                proposal_payload=context.proposal_payload,
                proposal_id=context.proposal_id,
                trip_changed=context.trip_changed,
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

    def route(state: MessagesState) -> Literal["execute", "end"]:
        last_message = state["messages"][-1]
        return "execute" if getattr(last_message, "tool_calls", None) else "end"

    def route_after_tools(state: MessagesState) -> Literal["continue", "end"]:
        if not generated_trip:
            return "continue"
        last_result = context.tool_events[-1]["result"]
        return "end" if last_result.get("ok") else "continue"

    builder = StateGraph(ConversationGraphState)
    builder.add_node("decide_action", agent_node)
    builder.add_node("execute_action", tool_node)
    builder.add_edge(START, "decide_action")
    builder.add_conditional_edges(
        "decide_action",
        route,
        {"execute": "execute_action", "end": END},
    )
    builder.add_conditional_edges(
        "execute_action",
        route_after_tools,
        {"continue": "decide_action", "end": END},
    )
    graph = builder.compile()
    state = graph.invoke(
        {"messages": initial_messages, "phase": phase},
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
