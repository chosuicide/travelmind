import re
from datetime import date, timedelta

from app.conversations.regions import (
    CITY_ALIASES,
    CITY_CODE_INDEX,
    PROVINCES,
    PROVINCE_ALIASES,
    resolve_city,
    resolve_province,
)
from app.conversations.schemas import DraftField, TripDraftPatch


REGION_FIELDS = {
    "province_code",
    "province_name",
    "city_code",
    "city_name",
}

SMALL_NUMBER_WORDS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}

CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def _chinese_amount(value: str) -> float | None:
    if not value or any(
        char not in CHINESE_DIGITS and char not in "十百千万"
        for char in value
    ):
        return None

    colloquial_tail = None
    if (
        len(value) >= 2
        and value[-1] in CHINESE_DIGITS
        and "零" not in value
        and "〇" not in value
    ):
        last_unit = next(
            (unit for unit in "千百" if unit in value[:-1]),
            "万" if "万" in value[:-1] else None,
        )
        if last_unit is not None:
            colloquial_tail = (
                CHINESE_DIGITS[value[-1]]
                * {"万": 1000, "千": 100, "百": 10}[last_unit]
            )
            value = value[:-1]

    total = 0
    section = 0
    number = 0
    for char in value:
        if char in CHINESE_DIGITS:
            number = CHINESE_DIGITS[char]
            continue
        unit = {"十": 10, "百": 100, "千": 1000, "万": 10_000}[char]
        if unit == 10_000:
            section = (section + number) * unit
            total += section
            section = 0
        else:
            section += (number or 1) * unit
        number = 0
    return float(total + section + number + (colloquial_tail or 0))


def _small_number(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    return SMALL_NUMBER_WORDS.get(value)


# === 模块：自然语言补丁兜底 ===
# 流程：短句/口语 → 保守识别日期、天数、人数、预算 → 与 AI 补丁合并
def detect_context_patch(
    current: dict,
    message: str,
    *,
    today: date | None = None,
) -> TripDraftPatch | None:
    compact = "".join(message.split())
    values: dict = {}
    reference_day = today or date.today()

    relative_offsets = {
        "今天": 0,
        "明天": 1,
        "后天": 2,
        "大后天": 3,
    }
    if "明后天" in compact:
        values["start_date"] = reference_day + timedelta(days=1)
        values["end_date"] = reference_day + timedelta(days=2)
    elif not any(word in compact for word in ("还是", "或者", "或是")):
        relative_mentions = [
            (match.start(), match.group(0), relative_offsets[match.group(0)])
            for match in re.finditer(r"大后天|后天|明天|今天", compact)
        ]
        if len(relative_mentions) >= 2:
            first = relative_mentions[0][2]
            second = relative_mentions[1][2]
            if second >= first:
                values["start_date"] = reference_day + timedelta(days=first)
                values["end_date"] = reference_day + timedelta(days=second)
        elif relative_mentions:
            relative_date = reference_day + timedelta(
                days=relative_mentions[0][2]
            )
            if current.get("start_date") and not current.get("end_date"):
                current_start = date.fromisoformat(str(current["start_date"]))
                if relative_date >= current_start:
                    values["end_date"] = relative_date
                else:
                    values["start_date"] = relative_date
            else:
                values["start_date"] = relative_date

    if "start_date" not in values:
        days_later_match = re.search(
            r"(\d+|[一二两三四五六七八九十])天后",
            compact,
        )
        if days_later_match:
            days_later = _small_number(days_later_match.group(1))
            if days_later is not None:
                values["start_date"] = reference_day + timedelta(
                    days=days_later
                )

    if "start_date" not in values:
        date_match = re.search(
            r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})[日号]",
            compact,
        )
        if date_match:
            year = int(date_match.group(1) or reference_day.year)
            candidate = date(
                year,
                int(date_match.group(2)),
                int(date_match.group(3)),
            )
            if date_match.group(1) is None and candidate < reference_day:
                candidate = candidate.replace(year=year + 1)
            if current.get("start_date") and not current.get("end_date"):
                values["end_date"] = candidate
            else:
                values["start_date"] = candidate

    duration_match = re.search(
        r"(?<!月)(\d+|[一二两三四五六七八九十])(?:天|日)(?!后)(?:游|行程|旅行|旅游)?",
        compact,
    )
    if duration_match:
        duration = _small_number(duration_match.group(1))
        if duration is not None:
            values["duration_days"] = duration

    people_match = re.search(
        r"(\d+|[一二两三四五六七八九十])(?:个)?人",
        compact,
    )
    if people_match:
        people = _small_number(people_match.group(1))
        if people is not None:
            values["people"] = people
    elif any(word in compact for word in ("独自", "一个人", "自己去")):
        values["people"] = 1
    elif current.get("people") is None:
        # 上一问是人数时，用户常只回答“五个”，无需强迫他说“五个人”。
        short_people_match = re.fullmatch(
            r"(\d+|[一二两三四五六七八九十])个",
            compact,
        )
        if short_people_match:
            people = _small_number(short_people_match.group(1))
            if people is not None:
                values["people"] = people

    if re.search(r"预算(?:不|无)(?:限|线)|不设预算|预算随意", compact):
        values["budget_flexible"] = True
    else:
        budget_match = re.search(
            r"预算(?:大概|差不多|控制在|是|就)?"
            r"(\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百千万]+)"
            r"(万|千|百|元|块|k|K)?",
            compact,
        )
        if budget_match is None and current.get("budget") is None:
            # 已经在回答预算时，允许“500”“五百元”这样的上下文短答。
            budget_match = re.fullmatch(
                r"(\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百千万]+)"
                r"(万|千|百|元|块|k|K)?",
                compact,
            )
        if budget_match:
            raw_budget = budget_match.group(1)
            budget = (
                float(raw_budget)
                if raw_budget[0].isdigit()
                else _chinese_amount(raw_budget)
            )
            unit = budget_match.group(2)
            if budget is None:
                return TripDraftPatch.model_validate(values) if values else None
            if unit == "万":
                budget *= 10_000
            elif unit in {"千", "k", "K"}:
                budget *= 1_000
            elif unit == "百":
                budget *= 100
            values["budget"] = budget

    return TripDraftPatch.model_validate(values) if values else None


def detect_interest_additions(message: str) -> list[str]:
    compact = "".join(message.split())
    matches = []
    for interest, words in {
        "美食": ("吃吃吃", "吃东西", "美食", "吃货", "逛吃"),
        "景点参观": ("参观景点", "参观一下景点", "看看景点", "逛景点"),
        "自然风景": ("山水", "自然风景", "看风景"),
        "历史文化": ("历史", "人文", "博物馆", "古迹"),
        "拍照打卡": ("拍照", "打卡", "出片"),
        "动漫": ("动漫", "二次元", "漫展"),
    }.items():
        if any(word in compact for word in words):
            matches.append(interest)
    return matches


def detect_draft_preview_action(message: str) -> str | None:
    compact = "".join(message.split()).rstrip("。！!？?")
    if compact in {
        "确认",
        "确认更新",
        "应用",
        "应用更新",
        "没问题",
        "可以",
        "就这样",
    }:
        return "apply"
    if compact in {
        "放弃",
        "放弃更新",
        "取消",
        "取消更新",
        "不要了",
        "算了",
    }:
        return "dismiss"
    return None


def delegates_planning(message: str) -> bool:
    """识别用户明确把剩余选择权交给 Agent 的表达。"""
    compact = "".join(message.split()).rstrip("。！!？?")
    return any(
        phrase in compact
        for phrase in (
            "随便",
            "都行",
            "你安排",
            "你来安排",
            "你看着办",
            "交给你",
            "按你推荐",
            "没有特别要求",
            "没什么要求",
        )
    )


def wants_generation(message: str) -> bool:
    compact = "".join(message.split()).rstrip("。！!？?")
    return any(
        phrase in compact
        for phrase in (
            "开始生成",
            "生成行程",
            "开始规划",
            "帮我安排",
            "你来安排",
            "你安排",
            "按这个来",
            "就按这个",
            "照这个安排",
            "直接生成",
            "确认生成",
            "就这样",
            "可以生成了",
            "出方案",
        )
    ) or compact in {"确认", "没问题", "可以了", "开始吧"}


# === 模块：用户原话地区兜底识别 ===
# 流程：扫描明确城市/省份 → 排除否定与歧义 → 返回官方 code/name 草稿补丁
def detect_region_patch(message: str) -> TripDraftPatch | None:
    compact = "".join(message.split())
    city_matches: dict[tuple[str, str], tuple[str, str, str]] = {}
    for alias, candidates in CITY_ALIASES.items():
        if len(alias) < 2 or alias not in compact:
            continue
        if f"不去{alias}" in compact or f"别去{alias}" in compact:
            continue
        for province_code, city_code, city_name in candidates:
            city_matches[(province_code, city_code)] = (
                province_code,
                city_code,
                city_name,
            )
    if len(city_matches) == 1:
        province_code, city_code, city_name = next(iter(city_matches.values()))
        return TripDraftPatch(
            province_code=province_code,
            province_name=PROVINCES[province_code],
            city_code=city_code,
            city_name=city_name,
        )
    if city_matches:
        return None

    province_matches = {
        value for alias, value in PROVINCE_ALIASES.items()
        if len(alias) >= 2 and alias in compact
        and f"不去{alias}" not in compact
        and f"别去{alias}" not in compact
    }
    if len(province_matches) == 1:
        province_code, province_name = next(iter(province_matches))
        return TripDraftPatch(
            province_code=province_code,
            province_name=province_name,
        )
    return None


# === 地区归一化：允许用户说简称，但只把官方代码和名称写入草稿 ===
# 流程：省/市简称 → 目录查找 → 冲突校验 → 官方 code/name 补丁
def normalize_patch(
    current: dict,
    patch: TripDraftPatch,
    clear_fields: list[DraftField] | None = None,
) -> tuple[TripDraftPatch, set[str]]:
    values = patch.model_dump(
        mode="json",
        exclude_unset=True,
        exclude_none=True,
    )
    clears = set(clear_fields or [])

    if {"province_code", "province_name"} & clears:
        clears.update(REGION_FIELDS)
    elif {"city_code", "city_name"} & clears:
        clears.update({"city_code", "city_name"})

    province_code = values.get("province_code")
    province_name = values.get("province_name")
    province_was_explicit = (
        province_code is not None or province_name is not None
    )
    resolved_province = None
    if province_code is not None:
        official_name = PROVINCES.get(province_code)
        if official_name is None:
            raise ValueError("province_code is not a supported mainland region")
        if province_name is not None:
            named_province = resolve_province(province_name)
            if named_province is None or named_province[0] != province_code:
                raise ValueError("province_name does not match province_code")
        resolved_province = (province_code, official_name)
    elif province_name is not None:
        resolved_province = resolve_province(province_name)
        if resolved_province is None:
            raise ValueError("province_name is not recognized")

    city_code = values.get("city_code")
    city_name = values.get("city_name")
    resolved_city = None
    region_scope = (
        resolved_province[0]
        if resolved_province is not None
        else current.get("province_code")
    )
    if city_code is not None:
        city_record = CITY_CODE_INDEX.get(city_code)
        if city_record is None:
            raise ValueError("city_code is not recognized")
        city_province_code, official_city_name = city_record
        if region_scope is not None and city_province_code != region_scope:
            raise ValueError("city_code does not belong to province_code")
        if city_name is not None:
            named_city = resolve_city(city_name, city_province_code)
            if named_city is None or named_city[1] != city_code:
                raise ValueError("city_name does not match city_code")
        resolved_city = (
            city_province_code,
            city_code,
            official_city_name,
        )
    elif city_name is not None:
        resolved_city = resolve_city(city_name, region_scope)
        if resolved_city is None and not province_was_explicit:
            resolved_city = resolve_city(city_name)
        if resolved_city is None:
            raise ValueError("city_name is not recognized or is ambiguous")

    if resolved_city is not None:
        city_province_code, city_code, city_name = resolved_city
        values["city_code"] = city_code
        values["city_name"] = city_name
        values["province_code"] = city_province_code
        values["province_name"] = PROVINCES[city_province_code]
        resolved_province = (
            city_province_code,
            PROVINCES[city_province_code],
        )
    elif resolved_province is not None:
        values["province_code"] = resolved_province[0]
        values["province_name"] = resolved_province[1]

    current_province = current.get("province_code")
    if (
        resolved_province is not None
        and current_province is not None
        and resolved_province[0] != current_province
        and resolved_city is None
    ):
        clears.update({"city_code", "city_name"})

    return TripDraftPatch.model_validate(values), clears
