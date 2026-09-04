from uuid import UUID

from cs.core.db import Database
from cs.core.ids import new_id


async def get_or_create_org(db: Database, clerk_org_id: str, name: str) -> UUID:
    row = await db.fetchrow("SELECT id FROM org WHERE clerk_org_id = $1", clerk_org_id)
    if row:
        return row["id"]
    oid = new_id()
    row = await db.fetchrow(
        """
        INSERT INTO org (id, name, clerk_org_id) VALUES ($1, $2, $3)
        ON CONFLICT (clerk_org_id) DO UPDATE SET name = org.name
        RETURNING id
        """,
        oid,
        name,
        clerk_org_id,
    )
    return row["id"]


async def ensure_membership(db: Database, org_id: UUID, user_id: str, role: str) -> None:
    await db.execute(
        """
        INSERT INTO user_org (user_id, org_id, role) VALUES ($1, $2, $3)
        ON CONFLICT (user_id, org_id) DO UPDATE SET role = EXCLUDED.role
        """,
        user_id,
        org_id,
        role,
    )
