import json
from pathlib import Path


# === 全国地区目录：后端持有省市代码与名称的唯一可信版本 ===
# 流程：加载版本化 JSON → 构建快速索引 → 精确验证省市组合 → 格式化目的地
CATALOG_PATH = Path(__file__).resolve().parent / "data" / "regions.json"
with CATALOG_PATH.open(encoding="utf-8") as catalog_file:
    REGION_CATALOG = json.load(catalog_file)

PROVINCES = {
    province["code"]: province["name"]
    for province in REGION_CATALOG["provinces"]
}
CITIES_BY_PROVINCE = {
    province_code: {
        city["code"]: city["name"]
        for city in cities
    }
    for province_code, cities in REGION_CATALOG["cities_by_province"].items()
}


def _region_aliases(name: str, suffixes: tuple[str, ...]) -> set[str]:
    aliases = {name}
    for suffix in suffixes:
        if name.endswith(suffix):
            aliases.add(name[: -len(suffix)])
    return aliases


PROVINCE_ALIASES: dict[str, tuple[str, str]] = {}
for code, name in PROVINCES.items():
    aliases = _region_aliases(name, ("壮族自治区", "回族自治区", "维吾尔自治区", "自治区", "省", "市"))
    for alias in aliases:
        PROVINCE_ALIASES[alias] = (code, name)

CITY_ALIASES: dict[str, list[tuple[str, str, str]]] = {}
CITY_CODE_INDEX: dict[str, tuple[str, str]] = {}
for province_code, cities in CITIES_BY_PROVINCE.items():
    for city_code, city_name in cities.items():
        CITY_CODE_INDEX[city_code] = (province_code, city_name)
        for alias in _region_aliases(
            city_name,
            ("自治州", "地区", "市", "盟"),
        ):
            CITY_ALIASES.setdefault(alias, []).append(
                (province_code, city_code, city_name)
            )


def validate_region(
    province_code: str | None,
    province_name: str | None,
    city_code: str | None,
    city_name: str | None,
) -> None:
    if province_code is not None:
        expected_province = PROVINCES.get(province_code)
        if expected_province is None:
            raise ValueError("province_code is not a supported mainland region")
        if province_name is not None and province_name != expected_province:
            raise ValueError("province_name does not match province_code")

    if city_code is not None:
        if province_code is None:
            raise ValueError("province_code is required with city_code")
        expected_city = CITIES_BY_PROVINCE.get(province_code, {}).get(city_code)
        if expected_city is None:
            raise ValueError("city_code does not belong to province_code")
        if city_name is not None and city_name != expected_city:
            raise ValueError("city_name does not match city_code")


def format_destination(province_name: str, city_name: str) -> str:
    return city_name if province_name == city_name else f"{province_name}{city_name}"


def list_provinces() -> list[dict]:
    return list(REGION_CATALOG["provinces"])


def list_cities(province_code: str) -> list[dict] | None:
    cities = REGION_CATALOG["cities_by_province"].get(province_code)
    return list(cities) if cities is not None else None


def resolve_province(value: str) -> tuple[str, str] | None:
    return PROVINCE_ALIASES.get(value.strip())


def resolve_city(
    value: str,
    province_code: str | None = None,
) -> tuple[str, str, str] | None:
    candidates = CITY_ALIASES.get(value.strip(), [])
    if province_code is not None:
        candidates = [
            candidate
            for candidate in candidates
            if candidate[0] == province_code
        ]
    return candidates[0] if len(candidates) == 1 else None
