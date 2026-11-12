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
from app.api.websocket import router as ws_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("beldex_sentinel")

STATIC_DIR = Path(__file__).resolve().parent / "static"

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing Beldex Nexus Rate Sentinel database...")
    await init_db()
    logger.info("Starting 24/7 background rate monitoring scheduler...")
    scheduler.start()
    yield
    logger.info("Shutting down background scheduler...")
    scheduler.stop()

app = FastAPI(
    title="Beldex Nexus Rate Sentinel",
    description="Cross-platform 30-minute Beldex (BDX) rate difference identifier, nexus.vin integrator & multi-channel selling alert system.",
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
    return {"message": "Beldex Sentinel API is running. UI not installed yet."}
