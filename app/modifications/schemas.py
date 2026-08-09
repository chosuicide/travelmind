from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.itinerary.schemas import (
    ItineraryOperation,
    ItineraryOperationsRequest,
)


# === 聊天修改决策：信息不足先追问，信息明确才生成操作 ===
# 流程：聊天上下文 + 当前行程 → clarify / proposal → 后端安全提案
class ModificationAgentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["clarify", "proposal"]
    assistant_message: str = Field(min_length=1, max_length=1200)
    operations: list[ItineraryOperation] = Field(
        default_factory=list,
        max_length=20,
    )

    @model_validator(mode="after")
    def validate_action_payload(self):
        if self.action == "proposal" and not self.operations:
            raise ValueError("proposal response requires operations")
        if self.action == "clarify" and self.operations:
            raise ValueError("clarify response cannot contain operations")
        return self

    def to_operations_request(self) -> ItineraryOperationsRequest:
        if self.action != "proposal":
            raise ValueError("clarification has no itinerary operations")
        return ItineraryOperationsRequest(operations=self.operations)
