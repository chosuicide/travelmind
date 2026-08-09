# === 生成策略：集中管理一次行程生成的产品边界 ===
# 流程：行程天数/节奏 → 读取统一规则 → Prompt、Agent、评测共同使用
MAX_TRIP_DAYS = 5

PACE_ACTIVITY_RANGES = {
    "relaxed": (1, 3),
    "balanced": (2, 4),
    "intensive": (3, 5),
}

TOOL_CALL_LIMITS_BY_MAX_DAYS = {
    1: {
        "search_places": 4,
        "get_place_details": 2,
        "estimate_route": 2,
        "check_itinerary": 2,
    },
    3: {
        "search_places": 8,
        "get_place_details": 3,
        "estimate_route": 4,
        "check_itinerary": 2,
    },
    5: {
        "search_places": 12,
        "get_place_details": 4,
        "estimate_route": 4,
        "check_itinerary": 2,
    },
}


def get_tool_call_limits(total_days: int) -> dict[str, int]:
    if not 1 <= total_days <= MAX_TRIP_DAYS:
        raise ValueError(
            f"Trip duration must be between 1 and {MAX_TRIP_DAYS} days"
        )

    for maximum_days, limits in TOOL_CALL_LIMITS_BY_MAX_DAYS.items():
        if total_days <= maximum_days:
            return limits.copy()

    raise RuntimeError("No tool-call policy matches the trip duration")
