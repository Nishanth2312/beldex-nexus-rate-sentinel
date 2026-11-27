import asyncio
from contextlib import asynccontextmanager
import logging
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db
from app.services.scheduler import scheduler
from app.api.routes_rates import router as rates_router
from app.api.routes_nexus import router as nexus_router
from app.api.routes_alerts import router as alerts_router
from app.api.routes_auth import router as auth_router
from app.api.websocket import router as ws_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("sentrix")

STATIC_DIR = Path(__file__).resolve().parent / "static"

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing Sentrix database...")
    await init_db()
    logger.info("Starting 24/7 background rate monitoring scheduler...")
    scheduler.start()
    yield
    logger.info("Shutting down background scheduler...")
    scheduler.stop()

app = FastAPI(
    title="Sentrix",
    description="Sentrix (Security, Precision and Intelligence) - Beldex (BDX) 10m/30m rate difference engine, nexus.vin selling system & multi-channel alerts.",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API Routers
app.include_router(auth_router)
app.include_router(rates_router)
app.include_router(nexus_router)
app.include_router(alerts_router)
app.include_router(ws_router)

# Mount Static UI Files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "scheduler_running": scheduler.is_running,
        "total_checks": scheduler.total_checks,
        "last_check_timestamp": scheduler.last_check_time
    }

@app.get("/")
async def serve_index():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {"message": "Sentrix API is running. UI not installed yet."}

@app.get("/{asset_name}")
async def serve_root_static_assets(asset_name: str):
    if asset_name in {"app.js", "styles.css", "bookmarklet.js"}:
        asset_path = STATIC_DIR / asset_name
        if asset_path.exists():
            return FileResponse(asset_path)
    from fastapi import HTTPException
    raise HTTPException(status_code=404, detail="Not Found")
