from fastapi import APIRouter, HTTPException

from app.conversations.regions import list_cities, list_provinces


router = APIRouter(prefix="/regions", tags=["regions"])


# === 地区目录接口：前端选择器与聊天校验读取同一份省市数据 ===
# 流程：读取省份 → 按 province_code 读取城市 → 前端提交官方代码与名称
@router.get("")
def read_provinces():
    return {"provinces": list_provinces()}


@router.get("/{province_code}/cities")
def read_cities(province_code: str):
    cities = list_cities(province_code)
    if cities is None:
        raise HTTPException(status_code=404, detail="Province not found")
    return {"province_code": province_code, "cities": cities}
