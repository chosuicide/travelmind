# === 地点服务：使用高德验证 AI 推荐的中国大陆真实 POI ===
# 流程：
# AI地点名称 + 目的地城市
# → 高德 POI 搜索
# → 找到真实地点
# → 返回 POI / 地址 / 经纬度
# → 查不到则返回 None

import os

import httpx
from dotenv import load_dotenv


load_dotenv()


AMAP_API_KEY = os.getenv("AMAP_API_KEY")

if not AMAP_API_KEY:
    raise RuntimeError("AMAP_API_KEY is not configured")


AMAP_PLACE_URL = "https://restapi.amap.com/v5/place/text"


# === 搜索真实地点 ===
# 流程：地点名 + 城市 → 高德 → 最相关 POI

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
            "page_size": 3,
        },
        timeout=10.0,
    )

    response.raise_for_status()

    data = response.json()

    # 高德自己的业务状态
    if data.get("status") != "1":
        raise RuntimeError(
            f"AMap error: {data.get('info')}"
        )

    pois = data.get("pois", [])

    if not pois:
        return None

    poi = pois[0]

    location_value = poi.get("location")

    if not location_value:
        return None

    longitude, latitude = location_value.split(",")

    return {
        "amap_id": poi.get("id"),
        "name": poi.get("name"),
        "address": poi.get("address"),
        "city": poi.get("cityname"),
        "district": poi.get("adname"),
        "latitude": float(latitude),
        "longitude": float(longitude),
    }

# === 验证整份 AI 行程里的真实地点 ===
# 流程：
# itinerary
# → 遍历所有 Activity
# → 高德逐个验证
# → 把真实坐标补回去
# → 任一地点不存在则拒绝

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


result = search_place(
    name="广州塔",
    location="海珠区",
    destination="广州市",
)

print(result)