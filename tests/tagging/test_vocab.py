import pytest

from cs.tagging import vocab
from cs.tagging.vocab import normalize


@pytest.mark.parametrize(
    "raw, slug",
    [
        ("Pharmacist Warning", "pharmacist_warning"),
        ("pharmacist-warning", "pharmacist_warning"),
        ("  Pharmacy--Coat  ", "pharmacy_coat"),
        ("Benadryl users!", "benadryl_users"),
        ("___", ""),
        ("x" * 60, "x" * 48),
        ("a" * 47 + "_b", "a" * 47),
    ],
)
def test_normalize(raw, slug):
    assert normalize(raw) == slug


async def test_canonicalize_inserts_new_and_reuses_existing(db, org_id):
    first = await vocab.canonicalize(
        db, org_id, "concept_tags", ["pharmacist_warning", "benadryl_swap", "pharmacist_warning"]
    )
    assert first == {"pharmacist_warning": "pharmacist_warning", "benadryl_swap": "benadryl_swap"}
    await db.execute(
        "UPDATE tag_vocab SET canonical_slug='benadryl_swap' "
        "WHERE org_id=$1 AND dimension='concept_tags' AND slug='pharmacist_warning'",
        org_id,
    )
    second = await vocab.canonicalize(
        db, org_id, "concept_tags", ["pharmacist_warning", "new_idea", ""]
    )
    assert second == {"pharmacist_warning": "benadryl_swap", "new_idea": "new_idea"}
    assert await db.fetchval("SELECT count(*) FROM tag_vocab WHERE org_id=$1", org_id) == 3


async def test_bump_counts_and_hints_order(db, org_id):
    await vocab.canonicalize(db, org_id, "topics", ["sleep", "magnesium", "zinc"])
    await vocab.bump_counts(db, org_id, "topics", ["sleep", "magnesium"])
    await vocab.bump_counts(db, org_id, "topics", ["sleep"])
    assert await vocab.hints(db, org_id, "topics", 10) == [("sleep", 2), ("magnesium", 1)]
    assert await vocab.hints(db, org_id, "topics", 1) == [("sleep", 2)]
    assert await vocab.hints(db, org_id, "claims", 10) == []


async def test_vocab_is_per_org_and_per_dimension(db, org_id):
    other = await db.fetchval(
        "INSERT INTO org (id, name) VALUES (gen_random_uuid(), 'o') RETURNING id"
    )
    await vocab.canonicalize(db, org_id, "topics", ["sleep"])
    await vocab.bump_counts(db, org_id, "topics", ["sleep"])
    assert await vocab.hints(db, other, "topics", 10) == []
    assert await vocab.hints(db, org_id, "claims", 10) == []
