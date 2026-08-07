# === AI 服务：负责调用 DeepSeek 生成结构化旅行计划 ===
# 流程：
# Trip 数据
# → 构建 Prompt
# → 调用 DeepSeek
# → 获取 JSON
# → Pydantic 验证
# → 返回可信的结构化数据给后端

import json
import os

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field


load_dotenv()

env_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path=env_path)

# === DeepSeek 客户端 ===

api_key = os.getenv("DEEPSEEK_API_KEY")

if not api_key:
    raise RuntimeError("DEEPSEEK_API_KEY is not configured")


client = OpenAI(
    api_key=api_key,
    base_url="https://api.deepseek.com",
    timeout=30.0,
)


# === AI 输出格式：限制模型必须返回我们能处理的数据 ===

class AIActivity(BaseModel):
    name: str = Field(min_length=1)
    location: str
    start_time: str
    end_time: str
    estimated_cost: float = Field(ge=0)
    description: str


class AIDay(BaseModel):
    day_number: int = Field(ge=1)
    summary: str
    activities: list[AIActivity]


class AIItinerary(BaseModel):
    days: list[AIDay]


# === 行程生成 ===
# Trip → Prompt → DeepSeek → JSON → 验证 → dict

def generate_itinerary(trip):
    total_days = (trip.end_date - trip.start_date).days + 1

    system_prompt = f"""
You are the itinerary planning engine for TravelMind.

Return ONLY valid JSON.

The trip has exactly {total_days} days.

Rules:
- Respect the user's budget, interests and travel pace.
- Generate exactly {total_days} days.
- day_number must start from 1.
- Keep the schedule realistic and not overly crowded.
- estimated_cost must never be negative.
- Do not include Markdown.
- The current version only supports mainland China destinations.
- Recommend specific real-world POIs that are searchable on AMap.
- Prefer official Chinese place names.
- Do not invent places.
- Every activity name must be a specific real-world place
  that can be searched on a map.
- Do not invent restaurants, attractions, stores, or landmarks.
- Avoid vague activity names such as "explore the city".
- Output JSON matching exactly this structure:

{{
  "days": [
    {{
      "day_number": 1,
      "summary": "short summary",
      "activities": [
        {{
          "name": "place or activity",
          "location": "location",
          "start_time": "10:00",
          "end_time": "12:00",
          "estimated_cost": 100,
          "description": "short description"
        }}
      ]
    }}
  ]
}}
"""

    user_prompt = f"""
Destination: {trip.destination}
Start date: {trip.start_date}
End date: {trip.end_date}
Budget: {trip.budget}
People: {trip.people}
Interests: {trip.interests}
Travel pace: {trip.pace}
Notes: {trip.notes}
"""

    response = client.chat.completions.create(
        model="deepseek-v4-flash",
        messages=[
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ],
        response_format={
            "type": "json_object"
        },
        extra_body={
            "thinking": {
                "type": "disabled"
            }
        },
        max_tokens=4000,
    )

    content = response.choices[0].message.content

    if not content:
        raise ValueError("DeepSeek returned empty content")

    raw_data = json.loads(content)

    # 不相信 LLM，先让 Pydantic 检查结构
    validated = AIItinerary.model_validate(raw_data)

    if len(validated.days) != total_days:
        raise ValueError(
            "DeepSeek returned incorrect number of days"
        )

    return validated.model_dump()