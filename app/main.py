from fastapi import FastAPI
from contextlib import asynccontextmanager
from app.config import get_settings
from app.api.webhooks import router as webhook_router
from app.services.followup_service import start_followup_service

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ────────────────────────────────────────────────────────────
    start_followup_service()
    yield
    # ── Shutdown ───────────────────────────────────────────────────────────
    # (cleanup opcional aqui se precisar)


app = FastAPI(
    title="DOSS AI BRAIN",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/")
def health_check():
    return {
        "status": "online",
        "system": "DOSS AI BRAIN",
        "environment": settings.ENVIRONMENT,
    }


app.include_router(webhook_router, prefix="/webhooks", tags=["Webhooks"])
