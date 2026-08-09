import re
import unicodedata
from difflib import SequenceMatcher

import httpx

from app.core.config import (
    AMAP_API_KEY,
    AMAP_DRIVING_ROUTE_URL,
    AMAP_PLACE_DETAIL_URL,
    AMAP_PLACE_URL,
    AMAP_TRANSIT_ROUTE_URL,
    AMAP_WALKING_ROUTE_URL,
)


if not AMAP_API_KEY:
    raise RuntimeError("AMAP_API_KEY is not configured")


MIN_PLACE_MATCH_SCORE = 60.0
PLACE_CATEGORY_TYPECODES = {
    "attraction": "110000",
    "restaurant": "050000",
}
SUPPORTED_ATTRACTION_TYPE_MARKERS = (
    "风景名胜",
    "博物馆",
    "展览馆",
    "纪念馆",
    "公园广场",
)
TRANSPORT_MISMATCH_MARKERS = (
    "地铁站",
    "公交站",
    "出入口",
    "入口",
    "出口",
    "停车场",
    "售票处",
)


def _string_value(value) -> str:
    if isinstance(value, list):
        return "".join(str(item) for item in value)
    return str(value or "")


def _normalize_text(value) -> str:
    text = unicodedata.normalize("NFKC", _string_value(value)).lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", text)


def _parenthetical_qualifiers(value) -> list[str]:
    normalized = unicodedata.normalize("NFKC", _string_value(value))
    return [
        _normalize_text(qualifier)
        for qualifier in re.findall(r"\(([^()]*)\)", normalized)
        if _normalize_text(qualifier)
    ]


def _bigram_similarity(first: str, second: str) -> float:
    if not first or not second:
        return 0.0

    def bigrams(value: str) -> set[str]:
        if len(value) < 2:
            return {value}
        return {
            value[index:index + 2]
            for index in range(len(value) - 1)
        }

    first_bigrams = bigrams(first)
    second_bigrams = bigrams(second)
    return (
        2 * len(first_bigrams & second_bigrams)
        / (len(first_bigrams) + len(second_bigrams))
    )


def _has_transport_mismatch(
    requested_name: str,
    candidate: dict,
) -> bool:
    candidate_text = _normalize_text(
        _string_value(candidate.get("name"))
        + _string_value(candidate.get("type"))
    )


def _type_score_adjustment(candidate: dict) -> float:
    typecodes = _string_value(candidate.get("typecode")).split("|")

    if any(typecode.startswith("1102") for typecode in typecodes):
        return 15.0
    if any(typecode.startswith("1101") for typecode in typecodes):
        return 8.0
    if any(typecode.startswith("05") for typecode in typecodes):
        return 5.0
    if any(typecode.startswith("190") for typecode in typecodes):
        return -15.0
    if any(typecode.startswith("130") for typecode in typecodes):
        return -15.0

    return 0.0

    return any(
        marker in candidate_text and marker not in requested_name
        for marker in TRANSPORT_MISMATCH_MARKERS
    )


# === POI 候选评分：综合名称、分店限定词、地址、行政区和类型 ===
# 流程：标准化候选 → 名称相似度 → 位置/门店校验 → 类型惩罚 → 匹配分数
def score_place_candidate(
    name: str,
    location: str,
    candidate: dict,
) -> float:
    requested_name = _normalize_text(name)
    requested_location = _normalize_text(location)
    candidate_name = _normalize_text(candidate.get("name"))
    candidate_address = _normalize_text(candidate.get("address"))
    candidate_district = _normalize_text(candidate.get("adname"))
    candidate_text = (
        candidate_name
        + candidate_district
        + candidate_address
    )

    if not requested_name or not candidate_name:
        return float("-inf")

    name_similarity = SequenceMatcher(
        None,
        requested_name,
        candidate_name,
    ).ratio()
    name_contains = (
        requested_name in candidate_name
        or candidate_name in requested_name
    )
    if name_similarity < 0.45 and not name_contains:
        return float("-inf")

    score = name_similarity * 45
    if requested_name == candidate_name:
        score += 35
    elif name_contains:
        score += 12

    score += _bigram_similarity(
        requested_location,
        candidate_text,
    ) * 45

    requested_districts = re.findall(
        r"[\u4e00-\u9fff]{2,}(?:区|县)",
        requested_location,
    )
    if requested_districts:
        if candidate_district in requested_location:
            score += 12
        else:
            score -= 12

    requested_numbers = re.findall(r"\d+", requested_location)
    if requested_numbers:
        if any(number in candidate_address for number in requested_numbers):
            score += 15
        else:
            score -= 15

    requested_qualifiers = _parenthetical_qualifiers(name)
    candidate_qualifiers = _parenthetical_qualifiers(
        candidate.get("name")
    )
    for qualifier in requested_qualifiers:
        if qualifier in candidate_text:
            score += 35
        elif candidate_qualifiers:
            score -= 50
        else:
            score -= 10

    if _has_transport_mismatch(
        requested_name,
        candidate,
    ):
        score -= 80

    score += _type_score_adjustment(candidate)

    return round(score, 2)


def select_best_place_candidate(
    name: str,
    location: str,
    candidates: list[dict],
) -> dict | None:
    scored_candidates = [
        (
            score_place_candidate(name, location, candidate),
            candidate,
        )
        for candidate in candidates
        if candidate.get("location")
    ]
    if not scored_candidates:
        return None

    best_score, best_candidate = max(
        scored_candidates,
        key=lambda item: item[0],
    )
    if best_score < MIN_PLACE_MATCH_SCORE:
        return None

    return best_candidate


def _serialize_place_candidate(poi: dict) -> dict | None:
    location_value = poi.get("location")
    if not location_value:
        return None

    try:
        longitude, latitude = location_value.split(",", maxsplit=1)
        latitude_value = float(latitude)
        longitude_value = float(longitude)
    except (AttributeError, TypeError, ValueError):
        return None

    if not poi.get("id") or not poi.get("name"):
        return None

    parent_id = _string_value(poi.get("parent"))
    business = poi.get("business")
    if not isinstance(business, dict):
        business = {}

    return {
        "amap_id": poi["id"],
        "name": _string_value(poi["name"]),
        "address": _string_value(poi.get("address")),
        "city": _string_value(poi.get("cityname")),
        "district": _string_value(poi.get("adname")),
        "latitude": latitude_value,
        "longitude": longitude_value,
        "type": _string_value(poi.get("type")),
        "typecode": _string_value(poi.get("typecode")),
        "adcode": _string_value(poi.get("adcode")),
        "citycode": _string_value(poi.get("citycode")),
        "parent_id": parent_id,
        "selection_role": "primary",
        "match_score": 0.0,
        "business": {
            key: _string_value(business.get(key))
            for key in (
                "opentime_today",
                "opentime_week",
                "rating",
                "cost",
                "tel",
                "tag",
                "keytag",
            )
            if business.get(key) not in (None, "", [])
        },
    }


def _fetch_place_details(
    place_ids: set[str],
    include_business: bool = False,
) -> list[dict]:
    if not place_ids:
        return []

    params = {
        "key": AMAP_API_KEY,
        "id": "|".join(sorted(place_ids)),
    }
    if include_business:
        params["show_fields"] = "business"

    response = httpx.get(
        AMAP_PLACE_DETAIL_URL,
        params=params,
        timeout=10.0,
    )
    response.raise_for_status()

    data = response.json()
    if data.get("status") != "1":
        raise RuntimeError(f"AMap error: {data.get('info')}")

    return [
        candidate
        for poi in data.get("pois", [])
        if (candidate := _serialize_place_candidate(poi)) is not None
    ]


def fetch_place_detail(place_id: str) -> dict | None:
    details = _fetch_place_details(
        {place_id},
        include_business=True,
    )
    return details[0] if details else None


def _route_coordinates(candidate: dict) -> str:
    try:
        longitude = float(candidate["longitude"])
        latitude = float(candidate["latitude"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Route POI is missing valid coordinates") from exc
    return f"{longitude:.6f},{latitude:.6f}"


def _parse_polyline_points(polyline: str) -> list[list[float]]:
    points = []
    for raw_point in polyline.split(";"):
        coordinates = raw_point.split(",")
        if len(coordinates) != 2:
            continue
        try:
            point = [float(coordinates[0]), float(coordinates[1])]
        except ValueError:
            continue
        if not points or points[-1] != point:
            points.append(point)
    return points


def _extract_route_polyline(value) -> list[list[float]]:
    if isinstance(value, dict):
        direct_polyline = value.get("polyline")
        if isinstance(direct_polyline, str) and direct_polyline:
            return _parse_polyline_points(direct_polyline)

        points = []
        for nested_value in value.values():
            for point in _extract_route_polyline(nested_value):
                if not points or points[-1] != point:
                    points.append(point)
        return points

    if isinstance(value, list):
        points = []
        for item in value:
            for point in _extract_route_polyline(item):
                if not points or points[-1] != point:
                    points.append(point)
        return points

    return []


# === 高德路线估算：只使用后端已记录 POI 的坐标和 ID 请求真实路线 ===
# 流程：可信起终点 → 出行方式端点 → 高德首选方案 → 距离/耗时/费用摘要
def estimate_place_route(
    origin: dict,
    destination: dict,
    mode: str,
) -> dict:
    if mode not in {"walking", "driving", "transit"}:
        raise ValueError(f"Unsupported route mode: {mode}")

    origin_coordinates = _route_coordinates(origin)
    destination_coordinates = _route_coordinates(destination)
    if mode == "walking":
        url = AMAP_WALKING_ROUTE_URL
        params = {
            "key": AMAP_API_KEY,
            "origin": origin_coordinates,
            "destination": destination_coordinates,
            "origin_id": origin["amap_id"],
            "destination_id": destination["amap_id"],
            "show_fields": "cost,polyline",
        }
    elif mode == "driving":
        url = AMAP_DRIVING_ROUTE_URL
        params = {
            "key": AMAP_API_KEY,
            "origin": origin_coordinates,
            "destination": destination_coordinates,
            "origin_id": origin["amap_id"],
            "destination_id": destination["amap_id"],
            "strategy": "32",
            "show_fields": "cost,polyline",
        }
    else:
        origin_citycode = origin.get("citycode")
        destination_citycode = destination.get("citycode")
        if not origin_citycode or not destination_citycode:
            raise ValueError("Transit route POIs are missing citycode")
        url = AMAP_TRANSIT_ROUTE_URL
        params = {
            "key": AMAP_API_KEY,
            "origin": origin_coordinates,
            "destination": destination_coordinates,
            "city": origin_citycode,
            "cityd": destination_citycode,
            "strategy": "0",
            "show_fields": "cost,polyline",
        }

    response = httpx.get(url, params=params, timeout=10.0)
    response.raise_for_status()
    data = response.json()
    if data.get("status") != "1":
        raise RuntimeError(f"AMap error: {data.get('info')}")

    route = data.get("route") or {}
    option_key = "transits" if mode == "transit" else "paths"
    options = route.get(option_key) or []
    if not options:
        raise ValueError("AMap returned no route option")
    option = options[0]
    duration_seconds = option.get("duration")
    if duration_seconds is None:
        duration_seconds = (option.get("cost") or {}).get("duration")

    estimated_cost = None
    if mode == "walking":
        estimated_cost = 0.0
    elif mode == "driving" and route.get("taxi_cost"):
        estimated_cost = float(route["taxi_cost"])
    elif mode == "transit" and option.get("cost"):
        estimated_cost = float(option["cost"])

    return {
        "origin_place_id": origin["amap_id"],
        "origin_name": origin["name"],
        "destination_place_id": destination["amap_id"],
        "destination_name": destination["name"],
        "mode": mode,
        "distance_meters": int(float(option["distance"])),
        "duration_minutes": (
            round(float(duration_seconds) / 60, 1)
            if duration_seconds is not None
            else None
        ),
        "estimated_cost": estimated_cost,
        "walking_distance_meters": (
            int(float(option["walking_distance"]))
            if option.get("walking_distance") is not None
            else None
        ),
        "polyline": _extract_route_polyline(option),
    }


# === 主体 POI 补全：父子关系与查询相关度共同决定哪个地点更适合被选择 ===
# 流程：文本候选 → 父级详情 → 名称/类型评分 → 每条父子链标记最佳匹配 → 排序
def _promote_primary_place_candidates(
    candidates: list[dict],
    keywords: str,
    limit: int,
) -> list[dict]:
    candidate_by_id = {
        candidate["amap_id"]: candidate
        for candidate in candidates
    }
    frontier = {
        candidate["parent_id"]
        for candidate in candidates
        if candidate["parent_id"]
    }

    try:
        for _ in range(2):
            missing_ids = frontier - set(candidate_by_id)
            if not missing_ids:
                break
            parents = _fetch_place_details(missing_ids)
            for parent in parents:
                candidate_by_id[parent["amap_id"]] = parent
            frontier = {
                parent["parent_id"]
                for parent in parents
                if parent["parent_id"]
            }
    except (httpx.HTTPError, RuntimeError):
        return candidates[:limit]

    query_parts = [keywords, *keywords.split()]
    normalized_parts = {
        _normalize_text(part)
        for part in query_parts
        if _normalize_text(part)
    }
    original_rank = {
        candidate["amap_id"]: index
        for index, candidate in enumerate(candidates)
    }

    def match_score(candidate: dict) -> float:
        candidate_name = _normalize_text(candidate["name"])
        similarities = [
            SequenceMatcher(
                None,
                query_part,
                candidate_name,
            ).ratio()
            for query_part in normalized_parts
        ]
        score = max(similarities, default=0.0) * 100
        if candidate_name in normalized_parts:
            score += 100
        elif any(
            query_part in candidate_name
            or candidate_name in query_part
            for query_part in normalized_parts
        ):
            score += 40

        typecode = candidate.get("typecode", "")
        if typecode.startswith("1102"):
            score += 15
        elif typecode.startswith("1101"):
            score += 5
        return round(score, 2)

    for candidate in candidate_by_id.values():
        candidate["match_score"] = match_score(candidate)

    lineage_by_root: dict[str, list[dict]] = {}
    for candidate in candidate_by_id.values():
        root = candidate
        for _ in range(2):
            parent = candidate_by_id.get(root["parent_id"])
            if parent is None:
                break
            root = parent
        lineage_by_root.setdefault(root["amap_id"], []).append(candidate)

    for lineage in lineage_by_root.values():
        best_match = max(
            lineage,
            key=lambda candidate: (
                candidate["match_score"],
                not candidate["parent_id"],
            ),
        )
        for candidate in lineage:
            candidate["selection_role"] = (
                "primary"
                if candidate["amap_id"] == best_match["amap_id"]
                else "sub_poi"
            )

    return sorted(
        candidate_by_id.values(),
        key=lambda candidate: (
            -candidate["match_score"],
            original_rank.get(candidate["amap_id"], len(candidates)),
        ),
    )[:limit]


# === Agent 地点搜索：按 AI 的关键词和分类返回真实、紧凑的高德候选 ===
# 流程：目的地锁定 → 关键词/行政区/分类查询 → 清洗坐标 → 去重/限量
def search_place_candidates(
    destination: str,
    keywords: str,
    district: str,
    category: str,
    limit: int = 5,
) -> list[dict]:
    if category not in PLACE_CATEGORY_TYPECODES:
        raise ValueError(f"Unsupported place category: {category}")
    if not 1 <= limit <= 5:
        raise ValueError("Place candidate limit must be between 1 and 5")

    response = httpx.get(
        AMAP_PLACE_URL,
        params={
            "key": AMAP_API_KEY,
            "keywords": f"{district} {keywords}",
            "region": destination,
            "city_limit": "true",
            "types": PLACE_CATEGORY_TYPECODES[category],
            "page_size": limit,
        },
        timeout=10.0,
    )
    response.raise_for_status()

    data = response.json()
    if data.get("status") != "1":
        raise RuntimeError(f"AMap error: {data.get('info')}")

    candidates = []
    seen_ids = set()
    for poi in data.get("pois", []):
        candidate = _serialize_place_candidate(poi)
        if candidate is None or candidate["amap_id"] in seen_ids:
            continue
        seen_ids.add(candidate["amap_id"])
        candidates.append(candidate)
        if len(candidates) >= limit:
            break

    if category == "attraction":
        return _promote_primary_place_candidates(
            candidates,
            keywords=keywords,
            limit=limit,
        )
    return candidates


# === 景点候选池发现：用城市热门词和用户兴趣多路召回真实 POI ===
# 流程：著名景点 + 兴趣查询 → 类型过滤 → 清洗/去重/限量 → 封闭候选池
def discover_attraction_candidates(
    destination: str,
    interests: list[str] | None = None,
    limit: int = 30,
) -> list[dict]:
    candidates = []
    seen_ids = set()
    queries = [f"{destination}著名景点"]
    queries.extend(
        f"{destination}{interest}景点"
        for interest in (interests or [])[:3]
    )

    for query_index, query in enumerate(queries):
        response = httpx.get(
            AMAP_PLACE_URL,
            params={
                "key": AMAP_API_KEY,
                "keywords": query,
                "region": destination,
                "city_limit": "true",
                "page_size": 15 if query_index == 0 else 10,
            },
            timeout=10.0,
        )
        response.raise_for_status()

        data = response.json()
        if data.get("status") != "1":
            raise RuntimeError(
                f"AMap error: {data.get('info')}"
            )

        for poi in data.get("pois", []):
            poi_type = _string_value(poi.get("type"))
            if not any(
                marker in poi_type
                for marker in SUPPORTED_ATTRACTION_TYPE_MARKERS
            ):
                continue

            candidate = _serialize_place_candidate(poi)
            if candidate is None or candidate["amap_id"] in seen_ids:
                continue
            seen_ids.add(candidate["amap_id"])
            candidates.append(candidate)

            if len(candidates) >= limit:
                return candidates

    return candidates


def _verified_place_data(candidate: dict) -> dict:
    return {
        key: candidate.get(key)
        for key in (
            "amap_id",
            "name",
            "address",
            "city",
            "district",
            "latitude",
            "longitude",
        )
    }


# === 候选池绑定：AI 只选择 ID，名称、地址和坐标由后端覆盖 ===
# 流程：AI place_provider_id → 候选池归属/去重 → 标准高德数据 → verified_place
def bind_itinerary_candidate_places(
    itinerary: dict,
    candidates: list[dict],
) -> dict:
    candidate_by_id = {
        candidate["amap_id"]: candidate
        for candidate in candidates
    }
    used_place_ids = set()

    for day in itinerary["days"]:
        for activity in day["activities"]:
            place_id = activity.get("place_provider_id")
            if place_id not in candidate_by_id:
                raise ValueError(
                    f"AI selected an unknown candidate POI: {place_id}"
                )
            if place_id in used_place_ids:
                raise ValueError(
                    f"AI selected a duplicate candidate POI: {place_id}"
                )

            used_place_ids.add(place_id)
            candidate = candidate_by_id[place_id]
            activity["name"] = candidate["name"]
            activity["location"] = (
                candidate["address"]
                or candidate["district"]
            )
            activity["verified_place"] = _verified_place_data(
                candidate
            )

    return itinerary


# === 高德地点搜索：召回多个候选，再由本地业务规则选择可信 POI ===
# 流程：地点名 + 城市 → 高德前 10 条 → 候选评分 → POI / None
def search_place(
    name: str,
    location: str,
    destination: str,
):
    response = httpx.get(
        AMAP_PLACE_URL,
        params={
            "key": AMAP_API_KEY,
            "keywords": name,
            "region": destination,
            "city_limit": "true",
            "page_size": 10,
        },
        timeout=10.0,
    )
    response.raise_for_status()

    data = response.json()
    if data.get("status") != "1":
        raise RuntimeError(
            f"AMap error: {data.get('info')}"
        )

    poi = select_best_place_candidate(
        name=name,
        location=location,
        candidates=data.get("pois", []),
    )
    if poi is None:
        return None

    candidate = _serialize_place_candidate(poi)
    if candidate is None:
        return None

    return _verified_place_data(candidate)


# === 整份行程地点验证：逐个活动补充可信高德 POI ===
# 流程：遍历 Activity → 多候选匹配 → 写入 verified_place → 失败则拒绝
def validate_itinerary_places(
    itinerary: dict,
    destination: str,
):
    for day in itinerary["days"]:
        for activity in day["activities"]:
            place = search_place(
                name=activity["name"],
                location=activity["location"],
                destination=destination,
            )

            if place is None:
                raise ValueError(
                    f"Unverified place: {activity['name']}"
                )

            activity["verified_place"] = place

    return itinerary
