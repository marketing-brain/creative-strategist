import json
from types import SimpleNamespace

from cs.connectors.base import PlatformCreative
from cs.core import repo as ingest_repo
from cs.core.embeddings import FakeEmbedder
from cs.core.ids import new_id
from cs.tagging import repo, vocab, vocab_merge
from cs.tagging.taxonomy import load_taxonomy
from cs.tagging.vocab_merge import (
    JUDGE_SYSTEM,
    FakeJudge,
    GatewayJudge,
    Pair,
    SlugContext,
    judge_schema,
    judge_user_text,
    slug_context,
)

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


async def _open_rows(db, org_id, dimension, values_with_evidence):
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
    rows = [
        {
            "dimension": dimension,
            "kind": "open",
            "value": v,
            "raw_value": v,
            "confidence": 1.0,
            "evidence": e,
        }
        for v, e in values_with_evidence
    ]
    await repo.finish_tag_run(db, org_id, rid, rows, "s", 1, 1)


async def test_slug_context_counts_and_up_to_three_evidence(db, org_id):
    await _seed_vocab(db, org_id, "claims", {"fall_asleep_naturally": 4, "stay_asleep": 1})
    await _open_rows(
        db,
        org_id,
        "claims",
        [("fall_asleep_naturally", f"e{i}") for i in range(5)] + [("stay_asleep", "s1")],
    )
    ctx = await slug_context(db, org_id, "claims", ["fall_asleep_naturally", "stay_asleep", "nope"])
    assert ctx["fall_asleep_naturally"].count == 4
    assert len(ctx["fall_asleep_naturally"].evidence) == 3
    assert ctx["stay_asleep"] == SlugContext(count=1, evidence=["s1"])
    assert ctx["nope"] == SlugContext(count=0, evidence=[])


def test_judge_prompt_and_schema():
    pairs = [Pair("glycinate", "magnesium_glycinate", 0.92)]
    ctx = {
        "glycinate": SlugContext(2, ["'glycinate' overlay at 0:12"]),
        "magnesium_glycinate": SlugContext(1, ["spoken at 0:40"]),
    }
    text = judge_user_text("features_mentioned", "Product features the ad calls out.", pairs, ctx)
    assert "Dimension: features_mentioned" in text and "Product features the ad calls out." in text
    assert "- glycinate (count 2): 'glycinate' overlay at 0:12" in text
    assert "- magnesium_glycinate (count 1): spoken at 0:40" in text
    assert "glycinate | magnesium_glycinate | cosine 0.92" in text
    assert "ingredient and a product containing it are different" in JUDGE_SYSTEM
    schema = judge_schema()
    item = schema["properties"]["pairs"]["items"]
    assert item["required"] == ["a", "b", "same_concept", "reason"]
    assert item["properties"]["same_concept"] == {"type": "boolean"}
    assert schema["additionalProperties"] is False


class _FakeCompletions:
    def __init__(self, text):
        self.text = text
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content=self.text, refusal=None),
                )
            ]
        )


async def test_gateway_judge_parses_verdicts_in_pair_order(settings):
    pairs = [Pair("a_slug", "b_slug", 0.9), Pair("c_slug", "d_slug", 0.8)]
    text = (
        '{"pairs": [{"a": "c_slug", "b": "d_slug", "same_concept": false, "reason": "different"}, '
        '{"a": "a_slug", "b": "b_slug", "same_concept": true, "reason": "same"}]}'
    )
    fake = _FakeCompletions(text)
    judge = GatewayJudge(settings, client=SimpleNamespace(chat=SimpleNamespace(completions=fake)))
    verdicts = await judge.judge("topics", "desc", pairs, {})
    assert [(v.pair, v.same_concept, v.reason) for v in verdicts] == [
        (pairs[0], True, "same"),
        (pairs[1], False, "different"),
    ]
    assert fake.calls[0]["model"] == settings.tagging_model
    assert fake.calls[0]["response_format"]["json_schema"]["strict"] is True


async def test_gateway_judge_missing_pair_is_a_rejection(settings):
    pairs = [Pair("a_slug", "b_slug", 0.9)]
    fake = _FakeCompletions('{"pairs": []}')
    judge = GatewayJudge(settings, client=SimpleNamespace(chat=SimpleNamespace(completions=fake)))
    verdicts = await judge.judge("topics", "desc", pairs, {})
    assert verdicts[0].same_concept is False and "no verdict" in verdicts[0].reason


async def test_fake_judge():
    pairs = [Pair("a_slug", "b_slug", 0.9), Pair("c_slug", "d_slug", 0.8)]
    verdicts = await FakeJudge({("b_slug", "a_slug")}).judge("topics", "d", pairs, {})
    assert [v.same_concept for v in verdicts] == [True, False]
