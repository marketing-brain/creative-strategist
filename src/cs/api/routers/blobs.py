import mimetypes

from fastapi import APIRouter, Depends, HTTPException, Response

from cs.api.deps import get_blobs
from cs.core.blobs import BlobStore, LocalBlobStore

router = APIRouter()


@router.get("/blobs/{key:path}")
async def get_blob(key: str, blobs: BlobStore = Depends(get_blobs)) -> Response:
    if not isinstance(blobs, LocalBlobStore):
        raise HTTPException(404, "blobs are served from object storage")
    try:
        if not await blobs.exists(key):
            raise HTTPException(404)
        data = await blobs.get(key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    media, _ = mimetypes.guess_type(key)
    return Response(content=data, media_type=media or "application/octet-stream")
