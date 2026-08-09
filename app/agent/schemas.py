from pydantic import BaseModel, ConfigDict, Field, model_validator


TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"


class AgentActivity(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    place_provider_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=200)
    location: str = Field(min_length=1, max_length=255)
    start_time: str = Field(pattern=TIME_PATTERN)
    end_time: str = Field(pattern=TIME_PATTERN)
    estimated_cost: float = Field(ge=0, le=1_000_000)
    description: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_time_order(self):
        if self.start_time >= self.end_time:
            raise ValueError("start_time must be earlier than end_time")
        return self


class AgentDay(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    day_number: int = Field(ge=1)
    summary: str = Field(min_length=1, max_length=500)
    activities: list[AgentActivity] = Field(min_length=1, max_length=6)


class AgentItinerary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: list[AgentDay] = Field(min_length=1)
