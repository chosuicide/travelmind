from fastapi import FastAPI

from app.auth.router import router as auth_router
from app.conversations.router import router as conversations_router
from app.itinerary.router import router as itinerary_router
from app.regions.router import router as regions_router
from app.trips.router import router as trips_router


# === 应用装配：这里只负责创建 FastAPI 并挂载各业务模块 ===
# 流程：创建应用 → 注册路由 → 由根入口交给 Uvicorn
app = FastAPI()

app.include_router(auth_router)
app.include_router(conversations_router)
app.include_router(regions_router)
app.include_router(trips_router)
app.include_router(itinerary_router)


@app.get("/")
def read_root():
    return {"message": "TravelMind API is running"}
