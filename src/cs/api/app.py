from fastapi import FastAPI

from cs.api.routers import (
    accounts,
    blobs,
    brand,
    copilot,
    creatives,
    generation,
    health,
    insights,
    meta_oauth,
    tags,
)
from cs.core.blobs import BlobStore
from cs.core.config import Settings
from cs.core.db import Database


def create_app(settings: Settings, db: Database, blobs_store: BlobStore) -> FastAPI:
    app = FastAPI(title="creative-strategist")
    app.state.settings = settings
    app.state.db = db
    app.state.blobs = blobs_store
    app.include_router(health.router)
    app.include_router(blobs.router)
    app.include_router(meta_oauth.router)
    app.include_router(accounts.router)
    app.include_router(creatives.router)
    app.include_router(tags.router)
    app.include_router(insights.router)
    app.include_router(brand.router)
    app.include_router(copilot.router)
    app.include_router(generation.router)
    return app
