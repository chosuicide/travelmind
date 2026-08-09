from datetime import datetime
from math import asin, cos, radians, sin, sqrt

from app.generation.policy import PACE_ACTIVITY_RANGES

MAX_STRAIGHT_LINE_TRANSFER_KM = 25.0


def _time_to_minutes(value: str) -> int | None:
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except (TypeError, ValueError):
        return None

    return parsed.hour * 60 + parsed.minute


def _check_time_ranges(days: list[dict]) -> tuple[bool, bool]:
    valid_ranges = True
    no_overlap = True

    for day in days:
        intervals = []
        for activity in day.get("activities", []):
            start = _time_to_minutes(activity.get("start_time"))
            end = _time_to_minutes(activity.get("end_time"))
            if start is None or end is None or start >= end:
                valid_ranges = False
                no_overlap = False
                continue
            intervals.append((start, end))

        intervals.sort()
        for previous, current in zip(intervals, intervals[1:]):
            if current[0] < previous[1]:
                no_overlap = False

    return valid_ranges, no_overlap


def _cost_metrics(days: list[dict]) -> tuple[bool, float | None]:
    total_cost = 0.0
    costs_are_valid = True

    for day in days:
        for activity in day.get("activities", []):
            try:
                cost = float(activity.get("estimated_cost"))
            except (TypeError, ValueError):
                costs_are_valid = False
                continue

            if cost < 0:
                costs_are_valid = False
            total_cost += cost

    if not costs_are_valid:
        return False, None

    return True, round(total_cost, 2)


def _all_places_verified(days: list[dict]) -> bool:
    activities = [
        activity
        for day in days
        for activity in day.get("activities", [])
    ]
    return bool(activities) and all(
        activity.get("verified_place") is not None
        for activity in activities
    )


def _coordinates(activity: dict) -> tuple[float, float] | None:
    verified_place = activity.get("verified_place") or {}
    try:
        latitude = float(verified_place["latitude"])
        longitude = float(verified_place["longitude"])
    except (KeyError, TypeError, ValueError):
        return None

    return latitude, longitude


def _straight_line_distance_km(
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    start_latitude, start_longitude = map(radians, start)
    end_latitude, end_longitude = map(radians, end)
    latitude_delta = end_latitude - start_latitude
    longitude_delta = end_longitude - start_longitude

    haversine_value = (
        sin(latitude_delta / 2) ** 2
        + cos(start_latitude)
        * cos(end_latitude)
        * sin(longitude_delta / 2) ** 2
    )
    return 6371.0 * 2 * asin(sqrt(haversine_value))


def _route_metrics(days: list[dict]) -> dict:
    daily_distances = []
    leg_distances = []
    missing_coordinates = 0

    for day in days:
        activities = day.get("activities", [])
        coordinates = [_coordinates(activity) for activity in activities]
        missing_coordinates += sum(
            coordinate is None
            for coordinate in coordinates
        )
        day_distances = []

        for start, end in zip(coordinates, coordinates[1:]):
            if start is None or end is None:
                continue
            distance = _straight_line_distance_km(start, end)
            day_distances.append(distance)
            leg_distances.append(distance)

        daily_distances.append(round(sum(day_distances), 2))

    return {
        "daily_straight_line_km": daily_distances,
        "max_transfer_straight_line_km": (
            round(max(leg_distances), 2)
            if leg_distances
            else 0.0
        ),
        "missing_coordinate_count": missing_coordinates,
    }


def _duplicate_verified_places(days: list[dict]) -> list[dict]:
    first_activity_by_place_id = {}
    duplicates = []

    for day in days:
        for activity in day.get("activities", []):
            verified_place = activity.get("verified_place") or {}
            place_id = verified_place.get("amap_id")
            if not place_id:
                continue

            activity_reference = {
                "day_number": day.get("day_number"),
                "activity_name": activity.get("name"),
                "verified_name": verified_place.get("name"),
            }
            if place_id in first_activity_by_place_id:
                duplicates.append(
                    {
                        "amap_id": place_id,
                        "first": first_activity_by_place_id[place_id],
                        "duplicate": activity_reference,
                    }
                )
            else:
                first_activity_by_place_id[place_id] = activity_reference

    return duplicates


def _quality_signals(
    trip,
    activity_counts: list[int],
    route_metrics: dict,
    duplicate_places: list[dict],
) -> dict:
    pace_range = PACE_ACTIVITY_RANGES.get(trip.pace)
    pace_matches = (
        pace_range is not None
        and bool(activity_counts)
        and all(
            pace_range[0] <= count <= pace_range[1]
            for count in activity_counts
        )
    )
    coordinates_complete = (
        route_metrics["missing_coordinate_count"] == 0
    )
    transfers_reasonable = (
        coordinates_complete
        and route_metrics["max_transfer_straight_line_km"]
        <= MAX_STRAIGHT_LINE_TRANSFER_KM
    )

    return {
        "pace_density_matches": _check(
            pace_matches,
            (
                f"pace {trip.pace}, expected activities/day "
                f"{pace_range}, got {activity_counts}"
            ),
        ),
        "reasonable_transfer_distances": _check(
            transfers_reasonable,
            (
                "heuristic limit "
                f"{MAX_STRAIGHT_LINE_TRANSFER_KM} km, max straight-line "
                f"transfer {route_metrics['max_transfer_straight_line_km']} "
                f"km, missing coordinates "
                f"{route_metrics['missing_coordinate_count']}"
            ),
        ),
        "unique_verified_places": _check(
            not duplicate_places,
            (
                "no repeated AMap POIs"
                if not duplicate_places
                else f"duplicate AMap POIs: {duplicate_places}"
            ),
        ),
    }


def _check(
    passed: bool,
    detail: str,
) -> dict:
    return {
        "passed": passed,
        "detail": detail,
    }


# === 行程确定性评分器：只评价程序能够重复计算的质量指标 ===
# 流程：Trip + 行程 → 天数/时间/费用/POI 检查 → 分数与诊断明细
def evaluate_itinerary(
    trip,
    itinerary: dict,
) -> dict:
    days = itinerary.get("days", [])
    expected_days = (trip.end_date - trip.start_date).days + 1
    expected_numbers = list(range(1, expected_days + 1))
    actual_numbers = [day.get("day_number") for day in days]
    activity_counts = [
        len(day.get("activities", []))
        for day in days
    ]

    valid_time_ranges, no_time_overlap = _check_time_ranges(days)
    costs_are_valid, total_activity_cost = _cost_metrics(days)
    all_places_verified = _all_places_verified(days)
    has_activities_every_day = (
        len(activity_counts) == expected_days
        and all(count > 0 for count in activity_counts)
    )
    activity_cost_within_budget = (
        costs_are_valid
        and total_activity_cost is not None
        and total_activity_cost <= float(trip.budget)
    )

    checks = {
        "correct_day_count": _check(
            len(days) == expected_days,
            f"expected {expected_days}, got {len(days)}",
        ),
        "sequential_day_numbers": _check(
            actual_numbers == expected_numbers,
            f"expected {expected_numbers}, got {actual_numbers}",
        ),
        "activities_present_every_day": _check(
            has_activities_every_day,
            f"activity counts by day: {activity_counts}",
        ),
        "valid_time_ranges": _check(
            valid_time_ranges,
            "every activity must use HH:MM with start before end",
        ),
        "no_time_overlap": _check(
            no_time_overlap,
            "activities within each day must not overlap",
        ),
        "non_negative_numeric_costs": _check(
            costs_are_valid,
            "every estimated_cost must be a non-negative number",
        ),
        "activity_cost_within_trip_budget": _check(
            activity_cost_within_budget,
            (
                f"activity cost {total_activity_cost}, "
                f"trip budget {trip.budget}"
            ),
        ),
        "all_places_verified": _check(
            all_places_verified,
            "every activity must contain AMap verified_place data",
        ),
    }

    passed_count = sum(
        check["passed"]
        for check in checks.values()
    )
    score = round(passed_count / len(checks) * 100, 1)
    route_metrics = _route_metrics(days)
    duplicate_places = _duplicate_verified_places(days)
    quality_signals = _quality_signals(
        trip,
        activity_counts,
        route_metrics,
        duplicate_places,
    )
    budget_utilization = (
        round(total_activity_cost / float(trip.budget), 4)
        if total_activity_cost is not None and float(trip.budget) > 0
        else None
    )

    return {
        "score": score,
        "passed_all_checks": passed_count == len(checks),
        "checks": checks,
        "quality_signals": quality_signals,
        "metrics": {
            "expected_days": expected_days,
            "actual_days": len(days),
            "activity_counts": activity_counts,
            "total_activity_cost": total_activity_cost,
            "activity_budget_utilization": budget_utilization,
            "duplicate_verified_places": duplicate_places,
            **route_metrics,
        },
    }
