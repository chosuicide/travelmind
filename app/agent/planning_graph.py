import json
from types import SimpleNamespace
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from app.agent.context import PlanningContext
from app.agent.prompts import build_agent_messages
from app.agent.quality import assess_itinerary_quality
from app.agent.schemas import AgentItinerary
from app.agent.tools import TRAVEL_TOOLS, format_tool_result


class PlanningGraphState(TypedDict):
    context: PlanningContext
    messages: list[dict]
    phase: Literal["research", "draft", "validate", "repair", "complete"]
    turn_count: int
    finalization_requested: bool
    repair_attempted: bool
    output_repair_attempted: bool
    pre_repair_itinerary: dict | None
    pre_repair_issues: list[str] | None
    model_message: object | None
    result: dict | None


def _assistant_message_dict(message) -> dict:
    result = {"role": "assistant", "content": message.content}
    if message.tool_calls:
        result["tool_calls"] = [
            {
                "id": tool_call.id,
                "type": "function",
                "function": {
                    "name": tool_call.function.name,
                    "arguments": tool_call.function.arguments,
                },
            }
            for tool_call in message.tool_calls
        ]
    return result


def _safe_tool_arguments(raw_arguments: str) -> dict:
    try:
        arguments = json.loads(raw_arguments)
    except (json.JSONDecodeError, TypeError):
        return {}
    return arguments if isinstance(arguments, dict) else {}


def _model_usage_dict(response) -> dict:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    return {
        "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
    }


def _with_tool_budget(payload: dict, context: PlanningContext) -> dict:
    return {**payload, "tool_budget": context.tool_budget_snapshot()}


def _quality_trip(trip, context: PlanningContext):
    return SimpleNamespace(
        pace=trip.pace,
        budget=trip.budget,
        checked_routes=context.routes_by_pair,
    )


# === 行程生成子图：显式编排模型、工具和质量验证 ===
# 流程：model → tools/validate → 修订回 model → 质量通过后结束
def run_planning_graph(agent, trip) -> dict:
    total_days = (trip.end_date - trip.start_date).days + 1
    context = PlanningContext(
        destination=trip.destination,
        pace=trip.pace,
        total_days=total_days,
        budget=float(trip.budget),
        max_tool_calls=agent.max_tool_calls,
    )

    def model_node(state: PlanningGraphState) -> dict:
        context = state["context"]
        if state["turn_count"] >= agent.max_model_turns:
            raise ValueError(
                f"Agent did not finish within {agent.max_model_turns} model turns"
            )
        messages = list(state["messages"])
        finalization_requested = state["finalization_requested"]
        tool_budget_exhausted = (
            context.tool_call_count >= context.max_tool_calls
        )
        if tool_budget_exhausted and not finalization_requested:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "The tool budget is exhausted. Do not request more "
                        "tools. Using only POIs already returned by tools, "
                        "produce the final itinerary JSON now."
                    ),
                }
            )
            finalization_requested = True

        force_final_response = (
            tool_budget_exhausted
            or state["repair_attempted"]
            or state["output_repair_attempted"]
        )
        must_search_first = (
            not context.places_by_id
            and not force_final_response
        )
        response = agent.client.chat.completions.create(
            model=agent.model,
            messages=messages,
            tools=TRAVEL_TOOLS,
            parallel_tool_calls=False,
            tool_choice=(
                "none"
                if force_final_response
                else {
                    "type": "function",
                    "function": {"name": "search_places"},
                }
                if must_search_first
                else "auto"
            ),
            response_format={"type": "json_object"},
            extra_body={"thinking": {"type": "disabled"}},
            max_tokens=4000,
        )
        if agent.on_model_usage:
            agent.on_model_usage(_model_usage_dict(response))
        message = response.choices[0].message
        message_data = _assistant_message_dict(message)
        messages.append(message_data)
        next_phase = (
            "research"
            if message_data.get("tool_calls")
            else "draft"
        )
        if agent.on_graph_event:
            agent.on_graph_event(
                {
                    "node": "model",
                    "status": "completed",
                    "turn": state["turn_count"] + 1,
                    "phase": next_phase,
                    "next": "tools" if next_phase == "research" else "validate",
                }
            )
        return {
            "messages": messages,
            "model_message": message_data,
            "turn_count": state["turn_count"] + 1,
            "finalization_requested": finalization_requested,
            "phase": next_phase,
        }

    def route_after_model(
        state: PlanningGraphState,
    ) -> Literal["tools", "validate"]:
        return (
            "tools"
            if state["model_message"].get("tool_calls")
            else "validate"
        )

    def validate_node(state: PlanningGraphState) -> dict:
        context = state["context"]
        if agent.on_graph_event:
            agent.on_graph_event(
                {
                    "node": "validate",
                    "status": "running",
                    "turn": state["turn_count"],
                    "phase": "validate",
                }
            )
        message = state["model_message"]
        if not message.get("content"):
            raise ValueError("DeepSeek returned empty agent content")
        messages = list(state["messages"])
        try:
            validated = AgentItinerary.model_validate(
                json.loads(message["content"])
            )
            if len(validated.days) != total_days:
                raise ValueError(
                    "incorrect number of days: expected "
                    f"{total_days}, got {len(validated.days)}"
                )
            expected_numbers = list(range(1, total_days + 1))
            actual_numbers = [day.day_number for day in validated.days]
            if actual_numbers != expected_numbers:
                raise ValueError(
                    "invalid day_number sequence: expected "
                    f"{expected_numbers}, got {actual_numbers}"
                )
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            if state["output_repair_attempted"]:
                raise ValueError(
                    "DeepSeek returned invalid itinerary after one repair "
                    f"attempt: {exc}"
                ) from exc
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"The itinerary JSON is invalid: {exc}. Rewrite the "
                        f"complete JSON once. It must contain exactly {total_days} "
                        "days numbered sequentially from 1. Do not call tools "
                        "and use only POI IDs already returned. Return only the "
                        "corrected JSON."
                    ),
                }
            )
            return {
                "messages": messages,
                "model_message": None,
                "phase": "repair",
                "output_repair_attempted": True,
                "finalization_requested": True,
            }

        itinerary = validated.model_dump()
        try:
            itinerary = context.bind_final_itinerary(itinerary)
        except ValueError as exc:
            if (
                state["repair_attempted"]
                and state["pre_repair_itinerary"] is not None
            ):
                if agent.on_quality_result:
                    agent.on_quality_result(
                        {"stage": "repair_rejected", "issues": [str(exc)]}
                    )
                return {"result": state["pre_repair_itinerary"]}
            if state["output_repair_attempted"]:
                raise
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"The itinerary uses an invalid POI: {exc}. Rewrite the "
                        "complete JSON once using only place_provider_id values "
                        "returned by tools. Do not call tools."
                    ),
                }
            )
            return {
                "messages": messages,
                "model_message": None,
                "phase": "repair",
                "output_repair_attempted": True,
                "finalization_requested": True,
            }

        quality_issues = assess_itinerary_quality(
            _quality_trip(trip, context),
            itinerary,
        )
        if agent.on_quality_result:
            agent.on_quality_result(
                {
                    "stage": (
                        "after_repair"
                        if state["repair_attempted"]
                        else "initial"
                    ),
                    "issues": quality_issues,
                }
            )
        if quality_issues and not state["repair_attempted"]:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Revise the complete itinerary once to fix the "
                        "deterministic quality issues below. Do not call tools. "
                        "Use only POI IDs already returned by tools. If no "
                        "suitable replacement exists, remove the problematic "
                        "activity while keeping each day within the requested "
                        "pace. Return only the complete corrected JSON.\n\n"
                        "Quality issues:\n"
                        + json.dumps(quality_issues, ensure_ascii=False)
                    ),
                }
            )
            return {
                "messages": messages,
                "model_message": None,
                "phase": "repair",
                "repair_attempted": True,
                "finalization_requested": True,
                "pre_repair_itinerary": itinerary,
                "pre_repair_issues": quality_issues,
            }
        if (
            state["repair_attempted"]
            and quality_issues
            and state["pre_repair_itinerary"] is not None
            and state["pre_repair_issues"] is not None
            and len(quality_issues) >= len(state["pre_repair_issues"])
        ):
            raise ValueError(
                "DeepSeek did not resolve the deterministic quality issues "
                "after one repair attempt"
            )
        return {"result": itinerary, "phase": "complete"}

    def tools_node(state: PlanningGraphState) -> dict:
        context = state["context"]
        if agent.on_graph_event:
            agent.on_graph_event(
                {
                    "node": "tools",
                    "status": "running",
                    "turn": state["turn_count"],
                }
            )
        messages = list(state["messages"])
        for tool_call in state["model_message"]["tool_calls"]:
            tool_name = tool_call["function"]["name"]
            raw_arguments = tool_call["function"]["arguments"]
            if context.tool_call_count >= context.max_tool_calls:
                if agent.on_tool_result:
                    agent.on_tool_result(
                        _with_tool_budget(
                            {
                                "tool_name": tool_name,
                                "arguments": _safe_tool_arguments(raw_arguments),
                                "candidate_count": 0,
                                "status": "rejected",
                                "error": "Tool budget exhausted",
                            },
                            context,
                        )
                    )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": json.dumps(
                            _with_tool_budget(
                                {
                                    "error": (
                                        "Tool budget exhausted. Use the "
                                        "candidates already returned."
                                    )
                                },
                                context,
                            )
                        ),
                    }
                )
                continue
            try:
                context.reserve_tool_call(tool_name)
            except ValueError as exc:
                if agent.on_tool_result:
                    agent.on_tool_result(
                        _with_tool_budget(
                            {
                                "tool_name": tool_name,
                                "arguments": _safe_tool_arguments(raw_arguments),
                                "candidate_count": 0,
                                "status": "rejected",
                                "error": str(exc),
                            },
                            context,
                        )
                    )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": json.dumps(
                            _with_tool_budget({"error": str(exc)}, context),
                            ensure_ascii=False,
                        ),
                    }
                )
                continue

            context.record_tool_name(tool_name)
            try:
                result = agent.tool_executor(tool_name, raw_arguments, context)
            except ValueError as exc:
                if agent.on_tool_result:
                    agent.on_tool_result(
                        _with_tool_budget(
                            {
                                "tool_name": tool_name,
                                "arguments": _safe_tool_arguments(raw_arguments),
                                "candidate_count": 0,
                                "status": "failed",
                                "error": str(exc),
                            },
                            context,
                        )
                    )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": json.dumps(
                            _with_tool_budget({"error": str(exc)}, context),
                            ensure_ascii=False,
                        ),
                    }
                )
                continue
            except Exception as exc:
                if agent.on_tool_result:
                    agent.on_tool_result(
                        _with_tool_budget(
                            {
                                "tool_name": tool_name,
                                "arguments": _safe_tool_arguments(raw_arguments),
                                "candidate_count": 0,
                                "status": "failed",
                                "error": type(exc).__name__,
                            },
                            context,
                        )
                    )
                raise

            observed_places = result.get("places", [])
            context.remember_places(observed_places)
            if agent.on_tool_result:
                agent.on_tool_result(
                    _with_tool_budget(
                        {
                            "tool_name": tool_name,
                            "arguments": json.loads(raw_arguments),
                            "candidate_count": len(observed_places),
                            "status": "succeeded",
                        },
                        context,
                    )
                )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": json.dumps(
                        _with_tool_budget(format_tool_result(result), context),
                        ensure_ascii=False,
                    ),
                }
            )
        return {
            "messages": messages,
            "model_message": None,
            "phase": "research",
        }

    def route_after_action(
        state: PlanningGraphState,
    ) -> Literal["continue", "end"]:
        return "end" if state["result"] is not None else "continue"

    builder = StateGraph(PlanningGraphState)
    builder.add_node("model", model_node)
    builder.add_node("tools", tools_node)
    builder.add_node("validate", validate_node)
    builder.add_edge(START, "model")
    builder.add_conditional_edges(
        "model",
        route_after_model,
        {"tools": "tools", "validate": "validate"},
    )
    builder.add_conditional_edges(
        "tools",
        route_after_action,
        {"continue": "model", "end": END},
    )
    builder.add_conditional_edges(
        "validate",
        route_after_action,
        {"continue": "model", "end": END},
    )
    checkpointer = None
    config = {"recursion_limit": agent.max_model_turns * 3 + 5}
    if agent.thread_id is not None:
        from app.agent.checkpoint import get_planning_checkpointer

        checkpointer = get_planning_checkpointer()
        config["configurable"] = {"thread_id": agent.thread_id}
    graph = builder.compile(checkpointer=checkpointer)
    initial_state = {
            "context": context,
            "messages": build_agent_messages(
                trip,
                max_tool_calls=agent.max_tool_calls,
            ),
            "phase": "research",
            "turn_count": 0,
            "finalization_requested": False,
            "repair_attempted": False,
            "output_repair_attempted": False,
            "pre_repair_itinerary": None,
            "pre_repair_issues": None,
            "model_message": None,
            "result": None,
    }
    graph_input = initial_state
    if checkpointer is not None:
        snapshot = graph.get_state(config)
        if snapshot.values:
            if snapshot.values.get("result") is not None:
                return snapshot.values["result"]
            graph_input = None
    final_state = graph.invoke(
        graph_input,
        config=config,
        durability="sync" if checkpointer is not None else None,
    )
    return final_state["result"]
