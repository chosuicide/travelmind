from dataclasses import dataclass, field

from app.generation.policy import get_tool_call_limits


# === Agent 规划上下文：记录本轮工具预算和 AI 真正见过的地点 ===
# 流程：预留调用额度 → 登记高德候选 → 校验最终 POI ID → 绑定可信地点数据
@dataclass
class PlanningContext:
    destination: str
    pace: str = "balanced"
    total_days: int = 1
    budget: float | None = None
    max_tool_calls: int = 8
    tool_call_count: int = 0
    places_by_id: dict[str, dict] = field(default_factory=dict)
    routes_by_pair: dict[tuple[str, str], dict] = field(
        default_factory=dict
    )
    tool_names_used: set[str] = field(default_factory=set)
    tool_call_counts: dict[str, int] = field(default_factory=dict)

    def tool_budget_snapshot(self) -> dict:
        limits = get_tool_call_limits(self.total_days)
        remaining_total = max(
            self.max_tool_calls - self.tool_call_count,
            0,
        )
        remaining_by_tool = {
            tool_name: min(
                max(
                    limit - self.tool_call_counts.get(tool_name, 0),
                    0,
                ),
                remaining_total,
            )
            for tool_name, limit in limits.items()
        }
        return {
            "remaining_total": remaining_total,
            "remaining_by_tool": remaining_by_tool,
        }

    def reserve_tool_call(self, tool_name: str | None = None) -> None:
        if self.tool_call_count >= self.max_tool_calls:
            raise ValueError(
                f"Agent exceeded the {self.max_tool_calls} tool-call limit"
            )
        if tool_name:
            limits = get_tool_call_limits(self.total_days)
            used = self.tool_call_counts.get(tool_name, 0)
            limit = limits.get(tool_name, self.max_tool_calls)
            if used >= limit:
                raise ValueError(
                    f"Agent exceeded the {limit}-call limit for {tool_name}"
                )
            self.tool_call_counts[tool_name] = used + 1
        self.tool_call_count += 1

    def record_tool_name(self, tool_name: str) -> None:
        self.tool_names_used.add(tool_name)

    def require_seen_place(self, place_id: str) -> dict:
        place = self.places_by_id.get(place_id)
        if place is None:
            raise ValueError(
                f"POI was not returned by search_places: {place_id}"
            )
        return place

    def remember_places(self, candidates: list[dict]) -> None:
        for candidate in candidates:
            place_id = candidate.get("amap_id")
            if place_id:
                self.places_by_id[place_id] = candidate

    def remember_route(
        self,
        origin_place_id: str,
        destination_place_id: str,
        route: dict,
    ) -> None:
        self.routes_by_pair[
            (origin_place_id, destination_place_id)
        ] = route

    def bind_final_itinerary(self, itinerary: dict) -> dict:
        used_place_ids: set[str] = set()

        for day in itinerary["days"]:
            for activity in day["activities"]:
                place_id = activity["place_provider_id"]
                if place_id not in self.places_by_id:
                    raise ValueError(
                        "AI selected a POI that was not returned by a tool: "
                        f"{place_id}"
                    )
                if place_id in used_place_ids:
                    raise ValueError(
                        f"AI selected a duplicate POI: {place_id}"
                    )

                used_place_ids.add(place_id)
                candidate = self.places_by_id[place_id]
                activity["name"] = candidate["name"]
                activity["location"] = (
                    candidate.get("address")
                    or candidate.get("district")
                    or self.destination
                )
                activity["verified_place"] = {
                    "amap_id": candidate["amap_id"],
                    "name": candidate["name"],
                    "address": candidate.get("address", ""),
                    "city": candidate.get("city", ""),
                    "district": candidate.get("district", ""),
                    "latitude": candidate.get("latitude"),
                    "longitude": candidate.get("longitude"),
                    "parent_id": candidate.get("parent_id", ""),
                    "selection_role": candidate.get(
                        "selection_role",
                        "primary",
                    ),
                    "match_score": candidate.get("match_score", 0.0),
                }

        return itinerary
