from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth.router import router as auth_router
from app.core.config import PROJECT_ROOT, SERVE_FRONTEND
from app.conversations.router import router as conversations_router
from app.itinerary.router import router as itinerary_router
from app.regions.router import router as regions_router
from app.trips.router import router as trips_router
from app.db.session import get_db


# === 应用装配：这里只负责创建 FastAPI 并挂载各业务模块 ===
# 流程：创建应用 → 注册路由 → 由根入口交给 Uvicorn
app = FastAPI()

app.include_router(auth_router)
app.include_router(conversations_router)
app.include_router(regions_router)
app.include_router(trips_router)
app.include_router(itinerary_router)


@app.get("/health")
def health_check(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
FRONTEND_INDEX = FRONTEND_DIST / "index.html"

if SERVE_FRONTEND:
    if not FRONTEND_INDEX.is_file():
        raise RuntimeError(
            "SERVE_FRONTEND is enabled but frontend/dist/index.html is missing"
        )

    assets_path = FRONTEND_DIST / "assets"
    app.mount("/assets", StaticFiles(directory=assets_path), name="assets")

    @app.get("/", include_in_schema=False)
    def serve_frontend_root():
        return FileResponse(FRONTEND_INDEX)

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_frontend_route(full_path: str):
        if full_path.split("/", 1)[0] in {
            "auth",
            "conversations",
            "regions",
            "trips",
        }:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Not found",
            )

        requested_file = (FRONTEND_DIST / full_path).resolve()
        if (
            requested_file.is_relative_to(FRONTEND_DIST.resolve())
            and requested_file.is_file()
        ):
            return FileResponse(requested_file)
        return FileResponse(FRONTEND_INDEX)
else:
    @app.get("/")
    def read_root():
        return {"message": "TravelMind API is running"}
