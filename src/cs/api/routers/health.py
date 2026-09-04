from fastapi import APIRouter, Depends

from cs.api.auth import AuthContext, current_auth

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"ok": True}


@router.get("/me")
async def me(auth: AuthContext = Depends(current_auth)) -> dict:
    return {"user_id": auth.user_id, "org_id": str(auth.org_id), "role": auth.role}
