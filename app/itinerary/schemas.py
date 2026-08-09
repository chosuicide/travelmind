from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"


# === 活动输入：手动编辑和 AI 提案共用的数据边界 ===
# 流程：JSON → Pydantic 校验 → 统一行程编辑器
class ActivityCreateInput(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    name: str = Field(min_length=1, max_length=200)
    location: str = Field(min_length=1, max_length=255)
    start_time: str | None = Field(default=None, pattern=TIME_PATTERN)
    end_time: str | None = Field(default=None, pattern=TIME_PATTERN)
    estimated_cost: float = Field(default=0, ge=0, le=1_000_000)
    description: str | None = Field(default=None, max_length=2000)


class ActivityUpdateInput(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    name: str | None = Field(default=None, min_length=1, max_length=200)
    location: str | None = Field(default=None, min_length=1, max_length=255)
    start_time: str | None = Field(default=None, pattern=TIME_PATTERN)
    end_time: str | None = Field(default=None, pattern=TIME_PATTERN)
    estimated_cost: float | None = Field(default=None, ge=0, le=1_000_000)
    description: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_changes(self):
        if not self.model_fields_set:
            raise ValueError("changes must contain at least one field")

        required_fields = {"name", "location", "estimated_cost"}
        for field_name in required_fields:
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")

        return self


# === 行程操作协议：通过 type 分流新增、修改、删除和移动 ===
# 流程：读取 type → 选择操作模型 → 校验字段 → 形成操作列表
class AddActivityOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["add_activity"]
    day_id: int = Field(gt=0)
    order: int | None = Field(default=None, ge=1)
    activity: ActivityCreateInput


class UpdateActivityOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["update_activity"]
    activity_id: int = Field(gt=0)
    changes: ActivityUpdateInput


class RemoveActivityOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["remove_activity"]
    activity_id: int = Field(gt=0)


class MoveActivityOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["move_activity"]
    activity_id: int = Field(gt=0)
    target_day_id: int = Field(gt=0)
    target_order: int = Field(ge=1)


ItineraryOperation = Annotated[
    AddActivityOperation
    | UpdateActivityOperation
    | RemoveActivityOperation
    | MoveActivityOperation,
    Field(discriminator="type"),
]


class ItineraryOperationsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operations: list[ItineraryOperation] = Field(
        min_length=1,
        max_length=20,
    )
