from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agent.context import PlanningContext
from app.integrations.amap import (
    estimate_place_route,
    fetch_place_detail,
    search_place_candidates,
)


class StrictToolArguments(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class SearchPlacesArguments(StrictToolArguments):
    keywords: str = Field(min_length=1, max_length=80)
    district: str = Field(min_length=1, max_length=50)
    category: Literal["attraction", "restaurant"]
    limit: int = Field(ge=1, le=5)


class GetPlaceDetailsArguments(StrictToolArguments):
    place_provider_id: str = Field(min_length=1, max_length=100)


class EstimateRouteArguments(StrictToolArguments):
    origin_place_id: str = Field(min_length=1, max_length=100)
    destination_place_id: str = Field(min_length=1, max_length=100)
    mode: Literal["walking", "driving", "transit"]


def _tool_definition(name: str, description: str, model) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": model.model_json_schema(),
        },
    }


# === Agent 三工具协议：模型提供查询参数，Graph 决定调用与验证阶段 ===
# 流程：Pydantic 参数边界 → 外部旅行能力 → Graph 强制验证
TRAVEL_TOOLS = [
    _tool_definition(
        "search_places",
        (
            "Search real mainland-China attractions or restaurants on "
            "AMap. This is the only tool that can introduce new POI IDs."
        ),
        SearchPlacesArguments,
    ),
    _tool_definition(
        "get_place_details",
        (
            "Get opening hours, rating, average cost and tags for one POI "
            "previously returned by search_places."
        ),
        GetPlaceDetailsArguments,
    ),
    _tool_definition(
        "estimate_route",
        (
            "Estimate a real walking, driving or transit route between two "
            "POIs previously returned by search_places."
        ),
        EstimateRouteArguments,
    ),
]


def _format_candidate(candidate: dict) -> dict:
    return {
        "place_provider_id": candidate["amap_id"],
        "name": candidate["name"],
        "address": candidate.get("address", ""),
        "district": candidate.get("district", ""),
        "type": candidate.get("type", ""),
        "selection_role": candidate.get("selection_role", "primary"),
        "parent_id": candidate.get("parent_id", ""),
        "match_score": candidate.get("match_score", 0.0),
        "latitude": candidate.get("latitude"),
        "longitude": candidate.get("longitude"),
    }


# === 工具执行器：所有非搜索工具只能使用本轮已经见过的 POI ===
# 流程：工具参数校验 → 上下文归属检查 → 高德/本地能力 → 统一 content/places
def execute_travel_tool(
    tool_name: str,
    raw_arguments: str,
    context: PlanningContext,
) -> dict:
    if tool_name == "search_places":
        arguments = SearchPlacesArguments.model_validate_json(raw_arguments)
        candidates = search_place_candidates(
            destination=context.destination,
            keywords=arguments.keywords,
            district=arguments.district,
            category=arguments.category,
            limit=arguments.limit,
        )
        return {
            "content": {
                "query": arguments.model_dump(),
                "candidates": [
                    _format_candidate(candidate)
                    for candidate in candidates
                ],
            },
            "places": candidates,
        }

    if tool_name == "get_place_details":
        arguments = GetPlaceDetailsArguments.model_validate_json(
            raw_arguments
        )
        known_place = context.require_seen_place(
            arguments.place_provider_id
        )
        detail = fetch_place_detail(arguments.place_provider_id)
        if detail is None:
            raise ValueError("AMap returned no place details")
        merged_place = {
            **known_place,
            **detail,
            "selection_role": known_place.get(
                "selection_role",
                "primary",
            ),
            "match_score": known_place.get("match_score", 0.0),
        }
        return {
            "content": {
                **_format_candidate(merged_place),
                "business": merged_place.get("business", {}),
            },
            "places": [merged_place],
        }

    if tool_name == "estimate_route":
        arguments = EstimateRouteArguments.model_validate_json(
            raw_arguments
        )
        if arguments.origin_place_id == arguments.destination_place_id:
            raise ValueError("Route origin and destination must differ")
        origin = context.require_seen_place(arguments.origin_place_id)
        destination = context.require_seen_place(
            arguments.destination_place_id
        )
        route = estimate_place_route(
            origin,
            destination,
            arguments.mode,
        )
        context.remember_route(
            arguments.origin_place_id,
            arguments.destination_place_id,
            route,
        )
        return {
            "content": route,
            "places": [],
        }

    raise ValueError(f"Unknown travel tool: {tool_name}")


def format_tool_result(result: dict) -> dict:
    return result["content"]
