from fastapi import Request

from cs.core.blobs import BlobStore
from cs.core.config import Settings
from cs.core.db import Database


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_blobs(request: Request) -> BlobStore:
    return request.app.state.blobs


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings
