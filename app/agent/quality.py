import re
from math import asin, cos, radians, sin, sqrt
from typing import Literal, TypedDict

from app.generation.policy import PACE_ACTIVITY_RANGES

MAX_URBAN_TRANSFER_KM = 6.0
UNSUITABLE_PLACE_MARKERS = (
    "不对外开放",
    "暂停营业",
    "停止营业",
    "已关闭",
    "停车场",
    "售票处",
    "游客中心",
    "出入口",
    "打卡点",
    "广场-",
)
ENGLISH_SENTENCE_PATTERN = re.compile(
    r"(?:\b[A-Za-z]{2,}\b[\s,.;:!?\-–—'\"]*){4,}"
)
MAX_SUMMARY_CHARACTERS = 24
MAX_DESCRIPTION_CHARACTERS = 100


class QualityIssue(TypedDict):
    code: str
    severity: Literal["warning", "error"]
    penalty: float
    message: str


def _issue(
    code: str,
    message: str,
    *,
    penalty: float,
    severity: Literal["warning", "error"] = "warning",
) -> QualityIssue:
    return {
        "code": code,
        "severity": severity,
        "penalty": round(max(0.0, penalty), 3),
        "message": message,
    }


def has_hard_quality_issues(issues: list[QualityIssue]) -> bool:
    return any(issue["severity"] == "error" for issue in issues)


def quality_penalty(issues: list[QualityIssue]) -> float:
    return round(
        sum(
            issue["penalty"]
            for issue in issues
            if issue["severity"] == "warning"
        ),
        3,
    )


def _looks_like_english_sentence(value: str | None) -> bool:
    return bool(value and ENGLISH_SENTENCE_PATTERN.search(value))


def _coordinates(activity: dict) -> tuple[float, float] | None:
    place = activity.get("verified_place") or {}
    try:
        return float(place["latitude"]), float(place["longitude"])
    except (KeyError, TypeError, ValueError):
        return None


def _distance_km(
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    start_latitude, start_longitude = map(radians, start)
    end_latitude, end_longitude = map(radians, end)
    latitude_delta = end_latitude - start_latitude
    longitude_delta = end_longitude - start_longitude
    value = (
        sin(latitude_delta / 2) ** 2
        + cos(start_latitude)
        * cos(end_latitude)
        * sin(longitude_delta / 2) ** 2
    )
    return 6371.0 * 2 * asin(sqrt(value))


def _compressed_districts(activities: list[dict]) -> list[str]:
    districts = []
    for activity in activities:
        district = (
            activity.get("verified_place") or {}
        ).get("district")
        if district and (not districts or district != districts[-1]):
            districts.append(district)
    return districts


def _time_minutes(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        hours, minutes = map(int, value.split(":"))
    except (AttributeError, TypeError, ValueError):
        return None
    return hours * 60 + minutes


# === Agent 质量检查器：硬错误守住数据底线，软问题用分数比较优化程度 ===
# 流程：行程 → 结构化问题 → 硬错误拦截 / 软问题计分 → Agent 择优保存
def assess_itinerary_quality(
    trip,
    itinerary: dict,
) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    pace_range = PACE_ACTIVITY_RANGES.get(trip.pace)
    total_cost = 0.0
    checked_routes = getattr(trip, "checked_routes", {})

    for day in itinerary["days"]:
        day_number = day["day_number"]
        activities = day["activities"]
        summary = day.get("summary")

        if _looks_like_english_sentence(summary):
            issues.append(
                _issue(
                    "summary_language",
                    f"Day {day_number} summary must be written in Simplified "
                    "Chinese, not an English sentence.",
                    penalty=4.0,
                )
            )
        if summary and len(summary) > MAX_SUMMARY_CHARACTERS:
            issues.append(
                _issue(
                    "summary_length",
                    f"Day {day_number} summary is too long; keep it within "
                    f"{MAX_SUMMARY_CHARACTERS} characters.",
                    penalty=(
                        1.0
                        + (len(summary) - MAX_SUMMARY_CHARACTERS)
                        / MAX_SUMMARY_CHARACTERS
                    ),
                )
            )

        if pace_range and not (
            pace_range[0] <= len(activities) <= pace_range[1]
        ):
            activity_delta = (
                pace_range[0] - len(activities)
                if len(activities) < pace_range[0]
                else len(activities) - pace_range[1]
            )
            issues.append(
                _issue(
                    "pace_activity_count",
                    f"Day {day_number} has {len(activities)} activities; "
                    f"{trip.pace} pace requires {pace_range[0]} to "
                    f"{pace_range[1]}.",
                    penalty=2.0 + activity_delta,
                )
            )

        for activity in activities:
            total_cost += float(activity.get("estimated_cost", 0))
            name = activity["name"]
            description = activity.get("description")
            if _looks_like_english_sentence(description):
                issues.append(
                    _issue(
                        "description_language",
                        f'Day {day_number} activity "{name}" description '
                        "must be written in Simplified Chinese, not an "
                        "English sentence.",
                        penalty=3.0,
                    )
                )
            if (
                description
                and len(description) > MAX_DESCRIPTION_CHARACTERS
            ):
                issues.append(
                    _issue(
                        "description_length",
                        f'Day {day_number} activity "{name}" description is '
                        "too long; keep it within "
                        f"{MAX_DESCRIPTION_CHARACTERS} characters.",
                        penalty=(
                            1.0
                            + (len(description) - MAX_DESCRIPTION_CHARACTERS)
                            / MAX_DESCRIPTION_CHARACTERS
                        ),
                    )
                )
            selection_role = (
                activity.get("verified_place") or {}
            ).get("selection_role")
            if selection_role == "sub_poi":
                issues.append(
                    _issue(
                        "sub_poi",
                        f'Day {day_number} selects sub-POI "{name}". Prefer '
                        "its primary parent POI already returned by tools.",
                        penalty=4.0,
                    )
                )
                continue

            marker = next(
                (
                    marker
                    for marker in UNSUITABLE_PLACE_MARKERS
                    if marker in name
                ),
                None,
            )
            if marker:
                issues.append(
                    _issue(
                        "unsuitable_poi",
                        f'Day {day_number} selects unsuitable or auxiliary '
                        f'POI "{name}" (marker: {marker}). Prefer a main, '
                        "visitor-accessible POI already returned by tools.",
                        penalty=4.0,
                    )
                )

        districts = _compressed_districts(activities)
        if len(districts) != len(set(districts)):
            issues.append(
                _issue(
                    "district_backtracking",
                    f"Day {day_number} backtracks between districts: "
                    f"{' -> '.join(districts)}. Reorder or replace "
                    "activities.",
                    penalty=2.0 + len(districts) - len(set(districts)),
                )
            )

        for first, second in zip(activities, activities[1:]):
            first_end = first.get("end_time")
            second_start = second.get("start_time")
            first_end_minutes = _time_minutes(first_end)
            second_start_minutes = _time_minutes(second_start)
            if (
                first_end_minutes is not None
                and second_start_minutes is not None
                and second_start_minutes < first_end_minutes
            ):
                overlap_minutes = first_end_minutes - second_start_minutes
                issues.append(
                    _issue(
                        "activity_overlap",
                        f'Day {day_number} has overlapping activities: '
                        f'"{first["name"]}" ends at {first_end}, '
                        f'but "{second["name"]}" starts at {second_start}.',
                        severity="error",
                        penalty=100.0 + overlap_minutes / 60,
                    )
                )

            first_coordinates = _coordinates(first)
            second_coordinates = _coordinates(second)
            if first_coordinates is None or second_coordinates is None:
                continue
            distance = _distance_km(
                first_coordinates,
                second_coordinates,
            )
            if distance > MAX_URBAN_TRANSFER_KM:
                route = checked_routes.get(
                    (
                        first.get("place_provider_id"),
                        second.get("place_provider_id"),
                    )
                )
                available_minutes = (
                    second_start_minutes - first_end_minutes
                    if first_end_minutes is not None
                    and second_start_minutes is not None
                    else None
                )
                route_minutes = (
                    float(route["duration_minutes"])
                    if route
                    and route.get("duration_minutes") is not None
                    else None
                )
                if (
                    route_minutes is not None
                    and available_minutes is not None
                    and route_minutes <= available_minutes
                ):
                    continue
                if route_minutes is not None:
                    shortfall = max(
                        0.0,
                        route_minutes - (available_minutes or 0),
                    )
                    issues.append(
                        _issue(
                            "transfer_time",
                            f'Day {day_number} route from "{first["name"]}" '
                            f'to "{second["name"]}" needs about '
                            f'{route_minutes:.1f} minutes, but the schedule '
                            f'leaves {available_minutes} minutes.',
                            penalty=2.0 + shortfall / 30,
                        )
                    )
                    continue
                issues.append(
                    _issue(
                        "transfer_distance",
                        f'Day {day_number} transfer from "{first["name"]}" '
                        f'to "{second["name"]}" is about {distance:.1f} km '
                        "straight-line. Prefer a nearer candidate or reorder.",
                        penalty=(
                            2.0
                            + (distance - MAX_URBAN_TRANSFER_KM)
                            / MAX_URBAN_TRANSFER_KM
                        ),
                    )
                )

    budget = getattr(trip, "budget", None)
    if budget is not None and total_cost > float(budget):
        budget_value = float(budget)
        issues.append(
            _issue(
                "budget_exceeded",
                f"Estimated activity cost {total_cost:.2f} exceeds the trip "
                f"budget {budget_value:.2f}.",
                penalty=(
                    2.0
                    + (total_cost - budget_value)
                    / max(abs(budget_value), 1.0)
                ),
            )
        )

    return issues
