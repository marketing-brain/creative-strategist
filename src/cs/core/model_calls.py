"""The ledger for paid model calls: claimed before the request, closed after it.

The queue says when work runs; it can never say whether money has already been spent. A
connection that dies without answering, a retry inside the SDK, a job that starts over -- each
of those looks identical to "nothing happened" from the outside, and the account read was
billed for all three in one night (2026-09-22).

So a row goes in before the request leaves. If the call never answers, the row stays and says
so. Callers ask `attempts_for` what has already been tried and decide; nothing here retries on
its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from cs.core.db import Database
from cs.core.ids import new_id


@dataclass(frozen=True)
class Claim:
    id: UUID
    attempt: int


async def attempts_for(db: Database, purpose: str, key: str) -> int:
    """How many times this exact piece of work has been sent to a paid model."""
    return int(
        await db.fetchval(
            "SELECT count(*) FROM model_call WHERE purpose=$1 AND key=$2", purpose, key
        )
        or 0
    )


async def claim(db: Database, org_id: UUID, purpose: str, key: str, model: str) -> Claim:
    """Record the request before it is sent. The attempt number is the ledger's own count,
    so two callers racing on the same key cannot both think they are the first."""
    row = await db.fetchrow(
        """
        INSERT INTO model_call (id, org_id, purpose, key, model, attempt)
        VALUES ($1, $2, $3, $4, $5,
                (SELECT count(*) + 1 FROM model_call WHERE purpose = $3 AND key = $4))
        RETURNING id, attempt
        """,
        new_id(),
        org_id,
        purpose,
        key,
        model,
    )
    assert row is not None
    return Claim(row["id"], row["attempt"])


async def finish(
    db: Database, claim_id: UUID, *, input_tokens: int = 0, output_tokens: int = 0
) -> None:
    await db.execute(
        "UPDATE model_call SET status='done', input_tokens=$2, output_tokens=$3,"
        " finished_at=now() WHERE id=$1",
        claim_id,
        input_tokens,
        output_tokens,
    )


async def fail(db: Database, claim_id: UUID, error: str) -> None:
    """A call that did not answer. It may still have been billed -- that is the point of the
    row -- so the status is 'failed', never a deletion."""
    await db.execute(
        "UPDATE model_call SET status='failed', error=$2, finished_at=now() WHERE id=$1",
        claim_id,
        error[:2000],
    )
