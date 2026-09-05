from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cs.api.auth import AuthContext, current_auth
from cs.api.deps import get_db, get_settings_dep
from cs.core.config import Settings
from cs.core.db import Database
from cs.insights import copilot

router = APIRouter(prefix="/copilot")


def default_copilot_factory(settings: Settings) -> copilot.Copilot:
    return copilot.ClaudeCopilot(settings)


COPILOT_FACTORY = default_copilot_factory


class NewConversation(BaseModel):
    title: str = "New conversation"


class NewMessage(BaseModel):
    content: str


@router.post("/conversations")
async def create(
    body: NewConversation, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)
) -> dict:
    return {"id": str(await copilot.create_conversation(db, auth.org_id, auth.user_id, body.title))}


@router.get("/conversations")
async def list_convs(
    auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)
) -> list[dict]:
    return await copilot.list_conversations(db, auth.org_id, auth.user_id)


async def _owned(db: Database, auth: AuthContext, conversation_id: UUID) -> None:
    ok = await db.fetchval(
        "SELECT 1 FROM copilot_conversation WHERE id=$1 AND org_id=$2 AND user_id=$3",
        conversation_id,
        auth.org_id,
        auth.user_id,
    )
    if not ok:
        raise HTTPException(404)


@router.get("/conversations/{conversation_id}")
async def messages(
    conversation_id: UUID, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)
) -> list[dict]:
    await _owned(db, auth, conversation_id)
    return await copilot.history(db, auth.org_id, conversation_id)


@router.post("/conversations/{conversation_id}/messages")
async def send(
    conversation_id: UUID,
    body: NewMessage,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    await _owned(db, auth, conversation_id)
    await copilot.append_message(db, auth.org_id, conversation_id, "user", body.content)
    hist = await copilot.history(db, auth.org_id, conversation_id)
    reply = await COPILOT_FACTORY(settings).answer(hist, copilot.CopilotTools(db, auth.org_id))
    await copilot.append_message(db, auth.org_id, conversation_id, "assistant", reply)
    return {"reply": reply}
