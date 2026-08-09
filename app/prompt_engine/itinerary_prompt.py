# === 行程业务提示层：根据旅行天数生成行程规划规则和输出协议 ===
# 流程：旅行天数 → 业务约束 → JSON 结构协议 → system message 内容
from app.prompt_engine.formatter import format_pace_activity_rules


ITINERARY_PROMPT_VERSION = "itinerary-v3-zh-cn-output"
CANDIDATE_POOL_PROMPT_VERSION = "itinerary-v3-amap-candidate-pool"


def build_itinerary_instructions(
    total_days: int,
    candidate_pool_enabled: bool = False,
) -> str:
    candidate_pool_rules = ""
    place_provider_id_field = ""
    pace_activity_rules = format_pace_activity_rules()
    if candidate_pool_enabled:
        candidate_pool_rules = """
- A closed list of verified AMap attraction POIs is provided by the backend.
- Every activity must select exactly one POI from that list.
- Copy its place_provider_id exactly. Never invent or alter an ID.
- Do not use a place that is absent from the candidate list.
- Use each place_provider_id at most once in the whole trip.
"""
        place_provider_id_field = (
            '          "place_provider_id": "AMap ID from the candidate list",\n'
        )

    return f"""
The trip has exactly {total_days} days.

Rules:
- Respect the user's budget, interests and travel pace.
- Generate exactly {total_days} days.
- day_number must start from 1.
- Keep the schedule realistic and not overly crowded.
- Match the number of activities to the requested travel pace:
{pace_activity_rules}
- Never schedule the same real-world POI more than once in the trip,
  even when it has another name or alias.
- Group each day's activities into nearby districts or areas.
- Avoid unnecessary backtracking between consecutive activities.
- Leave realistic travel time between consecutive activities.
{candidate_pool_rules}- estimated_cost must never be negative.
- Do not include Markdown.
- The current version only supports mainland China destinations.
- Recommend specific real-world POIs that are searchable on AMap.
- Prefer official Chinese place names.
- Do not invent places.
- Every activity name must be a specific real-world place
  that can be searched on a map.
- Do not invent restaurants, attractions, stores, or landmarks.
- Avoid vague activity names such as "explore the city".
- Write every user-visible generated sentence in natural Simplified Chinese.
- Keep official POI names and addresses exactly as provided; do not translate
  or romanize them.
- summary must be a concise Chinese theme of 6 to 18 Chinese characters.
- description must be a concise Chinese sentence of 15 to 60 Chinese
  characters.
- Do not write English sentences in summary, location, or description.
- Output JSON matching exactly this structure:

{{
  "days": [
    {{
      "day_number": 1,
      "summary": "岳阳楼与洞庭湖",
      "activities": [
        {{
          "name": "place or activity",
{place_provider_id_field}          "location": "location",
          "start_time": "10:00",
          "end_time": "12:00",
          "estimated_cost": 100,
          "description": "登临岳阳楼，远眺洞庭湖与君山景色。"
        }}
      ]
    }}
  ]
}}
"""
