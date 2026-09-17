import json

from cs.core.embeddings import FakeEmbedder
from cs.core.ids import new_id
from cs.tagging import vocab, vocab_merge

MODEL = "fake-embed"


async def _seed_vocab(db, org_id, dimension, counts: dict[str, int]):
    await vocab.canonicalize(db, org_id, dimension, list(counts))
    for slug, n in counts.items():
        await db.execute(
            "UPDATE tag_vocab SET count=$4 WHERE org_id=$1 AND dimension=$2 AND slug=$3",
            org_id,
            dimension,
            slug,
            n,
        )


def test_embed_text_uses_dimension_label_and_spaces():
    assert vocab_merge.embed_text("concept_tags", "benadryl_swap") == "concept tags: benadryl swap"


async def test_embed_missing_only_embeds_new_canonical_slugs(db, org_id):
    await _seed_vocab(db, org_id, "topics", {"sleep": 3, "magnesium": 2, "melatonin": 1})
    n = await vocab_merge.embed_missing(db, FakeEmbedder(), org_id, "topics", MODEL)
    assert n == 3
    assert await vocab_merge.embed_missing(db, FakeEmbedder(), org_id, "topics", MODEL) == 0
    # a merged (non-canonical) slug is not embedded
    await db.execute(
        "UPDATE tag_vocab SET canonical_slug='sleep' "
        "WHERE org_id=$1 AND dimension='topics' AND slug='melatonin'",
        org_id,
    )
    await vocab.canonicalize(db, org_id, "topics", ["insomnia"])
    assert await vocab_merge.embed_missing(db, FakeEmbedder(), org_id, "topics", MODEL) == 1
    stored = await db.fetchval(
        "SELECT embedding::text FROM tag_vocab_embedding "
        "WHERE org_id=$1 AND dimension='topics' AND slug='sleep'",
        org_id,
    )
    assert len(json.loads(stored)) == 1536


async def test_load_vectors_returns_canonical_slugs_only(db, org_id):
    await _seed_vocab(db, org_id, "topics", {"sleep": 3, "melatonin": 1})
    await vocab_merge.embed_missing(db, FakeEmbedder(), org_id, "topics", MODEL)
    await db.execute(
        "UPDATE tag_vocab SET canonical_slug='sleep' "
        "WHERE org_id=$1 AND dimension='topics' AND slug='melatonin'",
        org_id,
    )
    vectors = await vocab_merge.load_vectors(db, org_id, "topics", MODEL)
    assert set(vectors) == {"sleep"} and len(vectors["sleep"]) == 1536


async def test_candidate_pairs_respects_floor_and_rejections(db, org_id):
    await _seed_vocab(
        db,
        org_id,
        "features_mentioned",
        {"glycinate": 2, "magnesium_glycinate": 1, "sugar_free": 1},
    )
    await vocab_merge.embed_missing(db, FakeEmbedder(), org_id, "features_mentioned", MODEL)
    pairs = await vocab_merge.candidate_pairs(db, org_id, "features_mentioned", MODEL, floor=-1.0)
    assert len(pairs) == 3 and all(p.a < p.b for p in pairs)
    assert [p.cosine for p in pairs] == sorted((p.cosine for p in pairs), reverse=True)
    top = pairs[0]
    assert {top.a, top.b} == {"glycinate", "magnesium_glycinate"}
    high = await vocab_merge.candidate_pairs(
        db, org_id, "features_mentioned", MODEL, floor=top.cosine
    )
    assert high == [top]
    await db.execute(
        "INSERT INTO tag_vocab_merge (id, org_id, dimension, slug, into_slug, similarity, "
        "status, source) VALUES ($1,$2,'features_mentioned',$3,$4,$5,'rejected','auto')",
        new_id(),
        org_id,
        top.b,
        top.a,
        top.cosine,
    )
    pairs_wo_top = [p for p in pairs if p != top]
    assert (
        await vocab_merge.candidate_pairs(db, org_id, "features_mentioned", MODEL, floor=-1.0)
        == pairs_wo_top
    )
