import json

from app.generation.policy import PACE_ACTIVITY_RANGES


# === 节奏规则格式化：把统一策略转换成 Prompt 可读文本 ===
# 流程：生成策略 → 遍历节奏范围 → Prompt 规则列表
def format_pace_activity_rules() -> str:
    return "\n".join(
        f"- {pace} means {minimum} to {maximum} activities per day"
        for pace, (minimum, maximum) in PACE_ACTIVITY_RANGES.items()
    )


# === 行程上下文格式化：把 Trip 字段转换成模型可读的用户消息 ===
# 流程：Trip 属性 → 稳定字段顺序 → user message 内容
def format_trip_context(trip) -> str:
    return f"""
Destination: {trip.destination}
Start date: {trip.start_date}
End date: {trip.end_date}
Budget: {trip.budget}
People: {trip.people}
Interests: {trip.interests}
Travel pace: {trip.pace}
Notes: {trip.notes}
"""


# === 候选池格式化：只暴露 AI 安排行程所需的真实 POI 字段 ===
# 流程：高德候选 → 精简字段 → JSON → Prompt user context
def format_place_candidates(candidates: list[dict]) -> str:
    prompt_candidates = [
        {
            "place_provider_id": candidate["amap_id"],
            "name": candidate["name"],
            "district": candidate["district"],
            "address": candidate["address"],
            "type": candidate["type"],
        }
        for candidate in candidates
    ]
    return """
Available verified AMap attraction POIs:
{candidates}
""".format(
        candidates=json.dumps(
            prompt_candidates,
            ensure_ascii=False,
        )
    )
