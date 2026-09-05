from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel

from cs.api.auth import AuthContext, current_auth, require_admin
from cs.api.deps import get_blobs, get_db
from cs.core.blobs import BlobStore, sha256_hex
from cs.core.db import Database
from cs.generation import brand

router = APIRouter(prefix="/brand-kit")


class KitBody(BaseModel):
    colors: list[str] = []
    fonts: list[str] = []
    voice_rules: str = ""
    prohibited_claims: list[str] = []


async def _ser(k: brand.BrandKit, blobs: BlobStore) -> dict:
    return {"id": str(k.id), "version": k.version, "colors": k.colors, "fonts": k.fonts, "voice_rules": k.voice_rules,
            "prohibited_claims": k.prohibited_claims,
            "logo_url": await blobs.url_for(k.logo_path) if k.logo_path else None}


@router.get("")
async def get_kit(auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db), blobs: BlobStore = Depends(get_blobs)) -> dict:
    k = await brand.get_active_brand_kit(db, auth.org_id)
    if not k:
        raise HTTPException(404, "no brand kit yet")
    return await _ser(k, blobs)


@router.post("")
async def create_kit(body: KitBody, auth: AuthContext = Depends(require_admin), db: Database = Depends(get_db),
                     blobs: BlobStore = Depends(get_blobs)) -> dict:
    prev = await brand.get_active_brand_kit(db, auth.org_id)
    k = await brand.create_brand_kit(db, auth.org_id, body.colors, body.fonts, body.voice_rules, body.prohibited_claims,
                                     prev.logo_path if prev else None, None)
    return await _ser(k, blobs)


@router.post("/logo")
async def upload_logo(file: UploadFile, auth: AuthContext = Depends(require_admin), db: Database = Depends(get_db),
                      blobs: BlobStore = Depends(get_blobs)) -> dict:
    if file.content_type not in ("image/png", "image/svg+xml"):
        raise HTTPException(400, "logo must be PNG or SVG")
    data = await file.read()
    ext = "png" if file.content_type == "image/png" else "svg"
    key = f"{auth.org_id}/brand/logo.{ext}"
    await blobs.put(key, data, file.content_type)
    prev = await brand.get_active_brand_kit(db, auth.org_id)
    k = await brand.create_brand_kit(db, auth.org_id, prev.colors if prev else [], prev.fonts if prev else [],
                                     prev.voice_rules if prev else "", prev.prohibited_claims if prev else [], key, sha256_hex(data))
    return await _ser(k, blobs)
