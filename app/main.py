import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from app.database import init_db
from app.config import settings
from app.api.campaigns import router as campaigns_router
from app.api.leads import router as leads_router
from app.api.accounts import router as inboxes_router
from app.api.settings import router as settings_router
from app.api.stats import router as stats_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize SQLite schema and tables
    await init_db()
    print("[OpenExplee] Database initialized successfully.")
    yield


app = FastAPI(
    title="OpenExplee",
    description="The Open-Source, Self-Hosted Alternative to Explee",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS for local dev / remote frontends
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API Routers
app.include_router(campaigns_router)
app.include_router(leads_router)
app.include_router(inboxes_router)
app.include_router(settings_router)
app.include_router(stats_router)

# Mount Static Files
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def root():
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"name": "OpenExplee API", "status": "running", "version": "1.0.0"}


@app.get("/health")
async def health_check():
    return {"ok": True, "app": "openexplee", "version": "1.0.0"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=True)
