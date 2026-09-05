from fastapi import FastAPI

from cs.api.routers import blobs, health, meta_oauth
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
    return app
