from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.generation.policy import MAX_TRIP_DAYS


Interest = Annotated[
    str,
    Field(min_length=1, max_length=50),
]


# === 创建旅行请求：校验字段范围和日期业务规则 ===
# 流程：前端 JSON → 类型校验 → 日期关系 → 最长 5 天 → Trip
class TripCreate(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    destination: str = Field(min_length=2, max_length=100)
    start_date: date
    end_date: date
    budget: float = Field(gt=0, le=1_000_000)
    people: int = Field(ge=1, le=20)
    interests: list[Interest] = Field(min_length=1, max_length=10)
    pace: Literal["relaxed", "balanced", "intensive"]
    notes: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_trip_rules(self):
        if self.end_date < self.start_date:
            raise ValueError(
                "end_date must not be earlier than start_date"
            )

        total_days = (self.end_date - self.start_date).days + 1
        if total_days > MAX_TRIP_DAYS:
            raise ValueError(
                f"trip cannot be longer than {MAX_TRIP_DAYS} days"
            )

        return self
