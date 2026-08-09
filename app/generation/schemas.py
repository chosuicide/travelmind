from datetime import datetime

from pydantic import BaseModel, ConfigDict


class GenerationAcceptedResponse(BaseModel):
    trip_id: int
    run_id: int
    status: str


class GenerationRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    trip_id: int
    status: str
    model_name: str
    prompt_version: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    tool_call_count: int
    trace: list
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
