from datetime import date, timedelta

from pydantic import ValidationError

from app.conversations.schemas import TripDraft, TripDraftPatch
from app.trips.schemas import TripCreate
from app.conversations.regions import format_destination


REQUIRED_FIELDS = (
    "province_code",
    "province_name",
    "city_code",
    "city_name",
    "start_date",
    "end_date",
    "budget",
    "people",
)

DEFAULT_INTERESTS = ["城市漫游", "本地体验"]
DEFAULT_PACE = "balanced"
DEFAULT_PEOPLE = 1
FLEXIBLE_BUDGET_PER_PERSON_DAY = 2000


# === 对话状态机：草稿合并与 Trip 创建之间的业务防火墙 ===
# 流程：旧草稿 + 局部补丁 → 完整校验 → 缺失字段 → 可确认 Trip
def merge_draft(
    current: dict,
    patch: TripDraftPatch,
    *,
    clear_fields: set[str] | None = None,
    add_interests: list[str] | None = None,
    remove_interests: list[str] | None = None,
) -> TripDraft:
    merged = dict(current)
    for field_name in clear_fields or set():
        merged.pop(field_name, None)
    patch_values = patch.model_dump(
        mode="json",
        exclude_unset=True,
        exclude_none=True,
    )
    if "budget" in patch_values:
        merged.pop("budget_flexible", None)
        patch_values["budget_flexible"] = False
    elif patch_values.get("budget_flexible") is True:
        merged.pop("budget", None)
    if add_interests or remove_interests:
        patch_values.pop("interests", None)
    merged.update(patch_values)

    # “玩两天”可以先保存；日期随后补充时，再确定性算出结束日期。
    start_value = merged.get("start_date")
    duration_value = merged.get("duration_days")
    if "end_date" in patch_values and start_value:
        start = date.fromisoformat(str(start_value))
        end = date.fromisoformat(str(merged["end_date"]))
        merged["duration_days"] = (end - start).days + 1
    elif (
        start_value
        and duration_value
        and (
            "start_date" in patch_values
            or "duration_days" in patch_values
            or not merged.get("end_date")
        )
    ):
        start = date.fromisoformat(str(start_value))
        merged["end_date"] = (
            start + timedelta(days=int(duration_value) - 1)
        ).isoformat()
    interests = list(merged.get("interests") or [])
    for interest in add_interests or []:
        if interest not in interests:
            interests.append(interest)
    remove_set = set(remove_interests or [])
    if remove_set:
        interests = [item for item in interests if item not in remove_set]
    if (add_interests or remove_interests) and interests:
        merged["interests"] = interests
    elif (add_interests or remove_interests) and not interests:
        merged.pop("interests", None)
    return TripDraft.model_validate(merged)


def get_missing_fields(draft: dict | TripDraft) -> list[str]:
    data = (
        draft.model_dump(mode="json")
        if isinstance(draft, TripDraft)
        else draft
    )
    missing = [name for name in REQUIRED_FIELDS if data.get(name) is None]
    if data.get("budget_flexible") is True and "budget" in missing:
        missing.remove("budget")
    return missing


def conversation_status(draft: TripDraft) -> str:
    return "collecting" if get_missing_fields(draft) else "ready_to_confirm"


# === 模块：AI 默认候选值 ===
# 流程：数据库工作草稿 → 补齐可委托字段 → 标记 AI 默认 → 生成可撤销确认预览
def complete_preview_defaults(
    draft: TripDraft,
    *,
    reference_day: date | None = None,
) -> tuple[TripDraft, list[str]]:
    values = draft.model_dump(mode="json", exclude_none=True)
    assumed_fields: list[str] = []

    if not values.get("start_date"):
        if values.get("end_date"):
            values["start_date"] = values["end_date"]
        else:
            today = reference_day or date.today()
            days_until_saturday = (5 - today.weekday()) % 7 or 7
            values["start_date"] = (
                today + timedelta(days=days_until_saturday)
            ).isoformat()
        assumed_fields.append("start_date")
    if not values.get("end_date"):
        start = date.fromisoformat(str(values["start_date"]))
        duration = int(values.get("duration_days") or 2)
        values["end_date"] = (
            start + timedelta(days=duration - 1)
        ).isoformat()
        values["duration_days"] = duration
        assumed_fields.append("end_date")
    if values.get("people") is None:
        values["people"] = DEFAULT_PEOPLE
        assumed_fields.append("people")
    if values.get("budget") is None and values.get("budget_flexible") is not True:
        values["budget_flexible"] = True
        assumed_fields.append("budget")
    if not values.get("interests"):
        values["interests"] = DEFAULT_INTERESTS
        assumed_fields.append("interests")
    if not values.get("pace"):
        values["pace"] = DEFAULT_PACE
        assumed_fields.append("pace")

    return TripDraft.model_validate(values), assumed_fields


def build_trip_input(draft_data: dict) -> TripCreate:
    draft = TripDraft.model_validate(draft_data)
    missing = get_missing_fields(draft)
    if missing:
        raise ValueError(f"missing required fields: {', '.join(missing)}")
    data = draft.model_dump()
    total_days = (data["end_date"] - data["start_date"]).days + 1
    flexible_budget = data.get("budget_flexible") is True
    budget = data.get("budget")
    notes = data["notes"]
    if flexible_budget:
        budget = min(
            FLEXIBLE_BUDGET_PER_PERSON_DAY * data["people"] * total_days,
            1_000_000,
        )
        flexible_note = "预算可灵活安排，以体验质量为优先。"
        notes = f"{notes}\n{flexible_note}" if notes else flexible_note

    return TripCreate(
        destination=format_destination(
            data["province_name"],
            data["city_name"],
        ),
        start_date=data["start_date"],
        end_date=data["end_date"],
        budget=budget,
        people=data["people"],
        interests=data.get("interests") or DEFAULT_INTERESTS,
        pace=data.get("pace") or DEFAULT_PACE,
        notes=notes,
    )


def validation_message(exc: ValidationError) -> str:
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first["loc"])
    return f"{location}: {first['msg']}"
