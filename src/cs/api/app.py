from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from cs.api.routers import (
    accounts,
    blobs,
    connections,
    creatives,
    health,
    insights,
    tags,
)
from cs.core.blobs import BlobStore
from cs.core.config import Settings
from cs.core.db import Database


def create_app(settings: Settings, db: Database, blobs_store: BlobStore) -> FastAPI:
    app = FastAPI(title="creative-strategist")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.settings = settings
    app.state.db = db
    app.state.blobs = blobs_store
    app.include_router(health.router)
    app.include_router(blobs.router)
    app.include_router(connections.router)
    app.include_router(accounts.router)
    app.include_router(creatives.router)
    app.include_router(tags.router)
    app.include_router(insights.router)
    return app
