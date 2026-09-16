from cs.connectors.base import PlatformCreative
from cs.core import repo as ingest_repo
from cs.tagging import repo
from cs.tagging.taxonomy import load_taxonomy
from tests.conftest import dev_headers

H = dev_headers(org="org_tags")


async def _seed(client, db, settings):
    from uuid import UUID

    org_id = UUID((await client.get("/me", headers=H)).json()["org_id"])
    acct = await ingest_repo.upsert_ad_account(
        db,
        org_id,
        "1",
        "M",
        "USD",
    )
    cid, _ = await ingest_repo.upsert_creative(
        db,
        org_id,
        acct,
        PlatformCreative("c", "image", "H", "P", None, None, "u", None, None, {}),
        "raw",
        format_source="manual_review",
    )
    tv = await repo.ensure_taxonomy_version(db, load_taxonomy())
    rid = await repo.start_tag_run(db, org_id, cid, tv, "fake")
    await repo.finish_tag_run(
        db,
        org_id,
        rid,
        [{"dimension": "tone", "value": "warm", "confidence": 0.8, "evidence": "e"}],
        "s",
        1,
        1,
    )
    return cid


async def test_tags_endpoints(client, db, settings):
    cid = await _seed(client, db, settings)
    r = await client.get(f"/creatives/{cid}/tags", headers=H)
    assert r.json() == [
        {
            "dimension": "tone",
            "value": "warm",
            "confidence": 0.8,
            "evidence": "e",
            "kind": "single",
            "raw_value": None,
            "t_start_s": None,
            "ordinal": None,
        }
    ]
    r = await client.get("/tags/dimensions", headers=H)
    assert any(d["name"] == "hook_type" for d in r.json())
    r = await client.post(f"/creatives/{cid}/retag", headers=H)
    assert r.status_code == 200
    assert await db.fetchval("SELECT job_type FROM job") == "tagging.tag_creative"
    r = await client.post("/tagging/backfill", headers=H, json={"limit": 10})
    assert (
        r.status_code == 200
        and await db.fetchval("SELECT count(*) FROM job WHERE job_type='tagging.batch_submit'") == 1
    )


async def test_backfill_rejects_out_of_range_limit(client, db, settings):
    await _seed(client, db, settings)
    r = await client.post("/tagging/backfill", headers=H, json={"limit": -1})
    assert r.status_code == 422
    r = await client.post("/tagging/backfill", headers=H, json={"limit": 0})
    assert r.status_code == 422


async def test_retag_rejects_unresolved_format(client, db, settings):
    """An 'unresolved' creative (e.g. after the reclassification backfill, which
    deliberately leaves primary_asset_id set on image -> unresolved rows) must never be
    enqueued for tagging: taxonomy.for_format('unresolved') returns [], which turns into
    a JSON schema enum of [] that structured-output APIs reject -- a doomed job and a
    burned model call."""
    from uuid import UUID

    org_id = UUID((await client.get("/me", headers=H)).json()["org_id"])
    acct = await ingest_repo.upsert_ad_account(db, org_id, "1", "M", "USD")
    cid, _ = await ingest_repo.upsert_creative(
        db,
        org_id,
        acct,
        PlatformCreative("c_unresolved", "unresolved", "H", "P", None, None, "u", None, None, {}),
        "raw",
        format_source="unresolved",
    )

    r = await client.post(f"/creatives/{cid}/retag", headers=H)

    assert r.status_code == 422
    assert await db.fetchval("SELECT count(*) FROM job") == 0


async def test_v2_tag_endpoints(client, db, settings):
    from uuid import UUID

    from cs.tagging import vocab

    org_id = UUID((await client.get("/me", headers=H)).json()["org_id"])
    acct = await ingest_repo.upsert_ad_account(db, org_id, "1", "M", "USD")
    cid, _ = await ingest_repo.upsert_creative(
        db,
        org_id,
        acct,
        PlatformCreative("v", "video", "H", "P", None, None, "u", None, None, {}),
        "raw",
        format_source="manual_review",
    )
    tv = await repo.ensure_taxonomy_version(db, load_taxonomy())
    rid = await repo.start_tag_run(db, org_id, cid, tv, "fake")
    await repo.finish_tag_run(
        db,
        org_id,
        rid,
        [
            {
                "dimension": "messenger",
                "kind": "single",
                "value": "none",
                "confidence": 1.0,
                "evidence": "",
            },
            {
                "dimension": "transcript",
                "kind": "text",
                "value": "spoken words",
                "confidence": 1.0,
                "evidence": "",
            },
            {
                "dimension": "concept_tags",
                "kind": "open",
                "value": "benadryl_swap",
                "raw_value": "Benadryl swap",
                "confidence": 1.0,
                "evidence": "",
            },
        ],
        "s",
        1,
        1,
    )
    await vocab.canonicalize(db, org_id, "concept_tags", ["benadryl_swap", "pharmacist_warning"])
    await vocab.bump_counts(db, org_id, "concept_tags", ["benadryl_swap", "pharmacist_warning"])
    await vocab.bump_counts(db, org_id, "concept_tags", ["benadryl_swap"])

    r = await client.get(f"/creatives/{cid}/tags", headers=H)
    assert [t["dimension"] for t in r.json()] == ["concept_tags", "messenger"]
    assert r.json()[0]["kind"] == "open" and r.json()[0]["raw_value"] == "Benadryl swap"

    r = await client.get(f"/creatives/{cid}/transcript", headers=H)
    assert r.json() == {"transcript": "spoken words"}

    r = await client.get("/tags/dimensions", headers=H)
    kinds = {d["name"]: d["kind"] for d in r.json()}
    assert (
        kinds["messenger"] == "single"
        and kinds["concept_tags"] == "open"
        and kinds["hook_type"] == "single"
    )

    r = await client.get("/tags/dimensions/concept_tags/values", headers=H)
    assert r.json() == [
        {"value": "benadryl_swap", "count": 2},
        {"value": "pharmacist_warning", "count": 1},
    ]
    assert (await client.get("/tags/dimensions/messenger/values", headers=H)).status_code == 404
    assert (await client.get("/tags/dimensions/nope/values", headers=H)).status_code == 404


async def test_transcript_is_null_when_untagged(client, db, settings):
    cid = await _seed(client, db, settings)
    r = await client.get(f"/creatives/{cid}/transcript", headers=H)
    assert r.json() == {"transcript": None}
