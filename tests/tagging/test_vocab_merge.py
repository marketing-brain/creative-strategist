import json
from types import SimpleNamespace

import pytest

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
    Merge,
    Pair,
    SlugContext,
    Verdict,
    apply_merges,
    decide_review,
    judge_schema,
    judge_user_text,
    list_review,
    plan_merges,
    record_rejections,
    record_review,
    revert,
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


async def _open_rows(db, org_id, dimension, values_with_evidence, pcid="v"):
    acct = await ingest_repo.upsert_ad_account(db, org_id, "1", "M", "USD")
    cid, _ = await ingest_repo.upsert_creative(
        db,
        org_id,
        acct,
        PlatformCreative(pcid, "video", "H", "P", None, None, "u", None, None, {}),
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
    # the two over-merges dry run 1 produced, and the tie-breaker for everything else
    assert (
        "A claim about falling asleep and a claim about sleep quality or waking rested "
        "are different claims." in JUDGE_SYSTEM
    )
    assert (
        "A generic ingredient and a branded or specific form of it (an extract, a salt, "
        "a delivery system) are different." in JUDGE_SYSTEM
    )
    assert "When in doubt, answer false." in JUDGE_SYSTEM
    schema = judge_schema()
    item = schema["properties"]["pairs"]["items"]
    assert item["required"] == ["a", "b", "same_concept", "reason"]
    assert item["properties"]["same_concept"] == {"type": "boolean"}
    assert schema["additionalProperties"] is False


class _FakeCompletions:
    def __init__(self, text, finish_reason="stop", refusal=None, usage=(11, 22)):
        self.text = text
        self.finish_reason = finish_reason
        self.refusal = refusal
        self.usage = usage
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        usage = (
            SimpleNamespace(prompt_tokens=self.usage[0], completion_tokens=self.usage[1])
            if self.usage is not None
            else None
        )
        return SimpleNamespace(
            usage=usage,
            choices=[
                SimpleNamespace(
                    finish_reason=self.finish_reason,
                    message=SimpleNamespace(content=self.text, refusal=self.refusal),
                )
            ],
        )


def _judge(settings, fake):
    return GatewayJudge(settings, client=SimpleNamespace(chat=SimpleNamespace(completions=fake)))


async def test_gateway_judge_parses_verdicts_in_pair_order(settings):
    pairs = [Pair("a_slug", "b_slug", 0.9), Pair("c_slug", "d_slug", 0.8)]
    text = (
        '{"pairs": [{"a": "c_slug", "b": "d_slug", "same_concept": false, "reason": "different"}, '
        '{"a": "a_slug", "b": "b_slug", "same_concept": true, "reason": "same"}]}'
    )
    fake = _FakeCompletions(text)
    judge = _judge(settings, fake)
    verdicts = await judge.judge("topics", "desc", pairs, {})
    assert [(v.pair, v.same_concept, v.reason, v.answered) for v in verdicts] == [
        (pairs[0], True, "same", True),
        (pairs[1], False, "different", True),
    ]
    assert fake.calls[0]["model"] == settings.judge_model
    assert fake.calls[0]["response_format"]["json_schema"]["strict"] is True
    # token usage is recorded per call so the caller can report what the run cost
    assert judge.usage == [(11, 22)]


async def test_gateway_judge_refusal_and_truncation_raise(settings):
    """A truncated or refused response must raise, never be persisted as a rejection."""
    pairs = [Pair("a_slug", "b_slug", 0.9)]
    with pytest.raises(RuntimeError, match="truncated"):
        await _judge(settings, _FakeCompletions("{}", finish_reason="length")).judge(
            "topics", "desc", pairs, {}
        )
    with pytest.raises(RuntimeError, match="refused"):
        await _judge(settings, _FakeCompletions("{}", finish_reason="content_filter")).judge(
            "topics", "desc", pairs, {}
        )
    with pytest.raises(RuntimeError, match="refused"):
        await _judge(settings, _FakeCompletions("{}", refusal="I cannot help")).judge(
            "topics", "desc", pairs, {}
        )


async def test_gateway_judge_unanswered_pairs_are_not_rejections(settings):
    pairs = [Pair("a_slug", "b_slug", 0.9), Pair("c_slug", "d_slug", 0.8)]
    # the model echoes the slugs back swapped and recased -- still the same pair
    text = '{"pairs": [{"a": " B_Slug", "b": "A_SLUG ", "same_concept": true, "reason": "same"}]}'
    verdicts = await _judge(settings, _FakeCompletions(text)).judge("topics", "desc", pairs, {})
    assert (verdicts[0].same_concept, verdicts[0].answered) == (True, True)
    assert verdicts[1].answered is False
    assert verdicts[1].same_concept is False and "no verdict" in verdicts[1].reason


async def test_judge_in_batches_chunks_and_keeps_pair_order():
    pairs = [Pair(f"a{i:03d}", f"b{i:03d}", 0.9) for i in range(120)]

    class _Counting:
        def __init__(self):
            self.sizes = []
            self.inner = FakeJudge(set())

        async def judge(self, dimension, description, pairs, context):
            self.sizes.append(len(pairs))
            return await self.inner.judge(dimension, description, pairs, context)

    counting = _Counting()
    verdicts = await vocab_merge.judge_in_batches(counting, "topics", "desc", pairs, {})
    assert counting.sizes == [50, 50, 20]
    assert [v.pair for v in verdicts] == pairs
    assert await vocab_merge.judge_in_batches(counting, "topics", "desc", [], {}) == []


async def test_fake_judge():
    pairs = [Pair("a_slug", "b_slug", 0.9), Pair("c_slug", "d_slug", 0.8)]
    verdicts = await FakeJudge({("b_slug", "a_slug")}).judge("topics", "d", pairs, {})
    assert [v.same_concept for v in verdicts] == [True, False]


def _v(a, b, cos, same):
    return Verdict(Pair(*sorted((a, b)), cos), same, "r")


def test_plan_merges_union_find_canonical_by_count_then_length():
    counts = {
        "benadryl_swap": 3,
        "benadryl_sleep_alternative": 3,
        "benadryl_alternative": 1,
        "melatonin": 5,
    }
    verdicts = [
        _v("benadryl_swap", "benadryl_sleep_alternative", 0.85, True),
        _v("benadryl_alternative", "benadryl_sleep_alternative", 0.90, True),
        _v("melatonin", "benadryl_swap", 0.75, False),
    ]
    plan = plan_merges(verdicts, counts)
    # tie on count 3 -> shortest slug wins; each member logs its own strongest pair
    assert sorted((m.slug, m.into_slug, m.similarity) for m in plan.apply) == [
        ("benadryl_alternative", "benadryl_swap", 0.90),
        ("benadryl_sleep_alternative", "benadryl_swap", 0.90),
    ]
    assert plan.review == [] and [v.pair.a for v in plan.rejected] == ["benadryl_swap"]


def test_plan_merges_routes_low_cosine_groups_to_review():
    counts = {"gummy": 2, "easy_gummy_format": 4}
    plan = plan_merges([_v("gummy", "easy_gummy_format", 0.74, True)], counts)
    assert plan.apply == [] and [(m.slug, m.into_slug) for m in plan.review] == [
        ("gummy", "easy_gummy_format")
    ]


def test_plan_merges_same_concept_false_is_rejected_even_when_similar():
    # an unanswered pair is not a decision: it belongs to none of apply/review/rejected
    unanswered = Verdict(
        Pair("gummy", "melatonin", 0.91), False, "no verdict returned", answered=False
    )
    plan = plan_merges(
        [_v("magnesium", "magnesium_glycinate", 0.86, False), unanswered],
        {"magnesium": 5, "magnesium_glycinate": 1},
    )
    assert plan.apply == [] and plan.review == []
    assert [v.pair.a for v in plan.rejected] == ["magnesium"]
    assert plan.unanswered == [unanswered]


async def test_apply_merges_rewrites_vocab_and_open_tag_values_but_not_raw(db, org_id):
    await _seed_vocab(db, org_id, "features_mentioned", {"glycinate": 2, "magnesium_glycinate": 1})
    await _open_rows(
        db,
        org_id,
        "features_mentioned",
        [("glycinate", "e1"), ("magnesium_glycinate", "e2")],
    )
    await apply_merges(
        db,
        org_id,
        "features_mentioned",
        [Merge("magnesium_glycinate", "glycinate", 0.92, "same compound")],
        source="auto",
    )
    vocab_rows = {
        r["slug"]: (r["canonical_slug"], r["count"])
        for r in await db.fetch(
            "SELECT slug, canonical_slug, count FROM tag_vocab WHERE org_id=$1", org_id
        )
    }
    # count is distinct runs carrying the canonical, not the sum of the members' counts
    assert vocab_rows == {"glycinate": ("glycinate", 1), "magnesium_glycinate": ("glycinate", 0)}
    tags = await db.fetch(
        "SELECT value, raw_value FROM tag "
        "WHERE org_id=$1 AND dimension='features_mentioned' ORDER BY raw_value",
        org_id,
    )
    assert [(t["value"], t["raw_value"]) for t in tags] == [
        ("glycinate", "glycinate"),
        ("glycinate", "magnesium_glycinate"),
    ]
    log = await db.fetchrow(
        "SELECT slug, into_slug, status, source, reason FROM tag_vocab_merge WHERE org_id=$1",
        org_id,
    )
    assert tuple(log) == ("magnesium_glycinate", "glycinate", "applied", "auto", "same compound")
    # hints now show the merged count on the canonical only
    assert await vocab.hints(db, org_id, "features_mentioned", 10) == [("glycinate", 1)]


async def test_review_records_approve_and_reject(db, org_id):
    await _seed_vocab(db, org_id, "concept_tags", {"gummy": 2, "easy_gummy_format": 4})
    await _open_rows(db, org_id, "concept_tags", [("gummy", "e")])
    await record_review(
        db, org_id, "concept_tags", [Merge("gummy", "easy_gummy_format", 0.74, "maybe")]
    )
    await record_rejections(db, org_id, "concept_tags", [_v("gummy", "melatonin", 0.7, False)])
    pending = await list_review(db, org_id)
    assert [(r["slug"], r["into_slug"], r["status"]) for r in pending] == [
        ("gummy", "easy_gummy_format", "review")
    ]
    await decide_review(db, pending[0]["id"], approve=True)
    row = await db.fetchrow(
        "SELECT status, source, decided_at FROM tag_vocab_merge WHERE id=$1", pending[0]["id"]
    )
    assert (
        row["status"] == "applied" and row["source"] == "manual" and row["decided_at"] is not None
    )
    assert (
        await db.fetchval(
            "SELECT canonical_slug FROM tag_vocab "
            "WHERE org_id=$1 AND dimension='concept_tags' AND slug='gummy'",
            org_id,
        )
        == "easy_gummy_format"
    )
    assert (
        await db.fetchval(
            "SELECT value FROM tag WHERE org_id=$1 AND dimension='concept_tags'", org_id
        )
        == "easy_gummy_format"
    )
    assert await list_review(db, org_id) == []
    rejected = await db.fetchval(
        "SELECT count(*) FROM tag_vocab_merge WHERE org_id=$1 AND status='rejected'", org_id
    )
    assert rejected == 1


async def test_decide_review_reject_and_revert(db, org_id):
    await _seed_vocab(db, org_id, "concept_tags", {"gummy": 2, "easy_gummy_format": 4})
    await _open_rows(db, org_id, "concept_tags", [("gummy", "e"), ("easy_gummy_format", "f")])
    await record_review(
        db, org_id, "concept_tags", [Merge("gummy", "easy_gummy_format", 0.74, "maybe")]
    )
    (pending,) = await list_review(db, org_id)
    await decide_review(db, pending["id"], approve=False)
    assert (
        await db.fetchval("SELECT status FROM tag_vocab_merge WHERE id=$1", pending["id"])
        == "rejected"
    )
    # a rejected pair no longer surfaces as a candidate (Task 2 contract)
    await vocab_merge.embed_missing(db, FakeEmbedder(), org_id, "concept_tags", MODEL)
    assert await vocab_merge.candidate_pairs(db, org_id, "concept_tags", MODEL, floor=-1.0) == []
    # apply then revert restores vocab and tag values from raw_value
    await apply_merges(
        db, org_id, "concept_tags", [Merge("gummy", "easy_gummy_format", 0.9, "r")], source="manual"
    )
    await revert(db, org_id, "concept_tags", "gummy")
    assert (
        await db.fetchval(
            "SELECT canonical_slug FROM tag_vocab "
            "WHERE org_id=$1 AND dimension='concept_tags' AND slug='gummy'",
            org_id,
        )
        == "gummy"
    )
    values = sorted(
        r["value"]
        for r in await db.fetch(
            "SELECT value FROM tag WHERE org_id=$1 AND dimension='concept_tags'", org_id
        )
    )
    assert values == ["easy_gummy_format", "gummy"]
    counts = {
        r["slug"]: r["count"]
        for r in await db.fetch("SELECT slug, count FROM tag_vocab WHERE org_id=$1", org_id)
    }
    assert counts == {"gummy": 1, "easy_gummy_format": 1}  # recomputed: one run carries each
    assert (
        await db.fetchval(
            "SELECT status FROM tag_vocab_merge "
            "WHERE org_id=$1 AND slug='gummy' AND status='reverted'",
            org_id,
        )
        == "reverted"
    )
    # and the reverted pair is not proposed again
    pairs = await vocab_merge.candidate_pairs(db, org_id, "concept_tags", MODEL, floor=-1.0)
    assert ("easy_gummy_format", "gummy") not in [(p.a, p.b) for p in pairs]


def test_plan_merges_logs_each_members_strongest_evidence():
    counts = {"sleep_aid": 9, "sleep_support": 1, "sleepy": 1}
    verdicts = [
        Verdict(Pair("sleep_aid", "sleep_support", 0.95), True, "same aid"),
        Verdict(Pair("sleep_support", "sleepy", 0.82), True, "same vibe"),
    ]
    plan = plan_merges(verdicts, counts)
    # not the group's weakest (0.82) for both, and not the first verdict's reason for both
    assert sorted((m.slug, m.similarity, m.reason) for m in plan.apply) == [
        ("sleep_support", 0.95, "same aid"),
        ("sleepy", 0.82, "same vibe"),
    ]


async def test_apply_merges_counts_distinct_runs_across_creatives(db, org_id):
    await _seed_vocab(db, org_id, "features_mentioned", {"glycinate": 2, "magnesium_glycinate": 1})
    await _open_rows(
        db, org_id, "features_mentioned", [("glycinate", "e1"), ("magnesium_glycinate", "e2")]
    )
    await _open_rows(db, org_id, "features_mentioned", [("magnesium_glycinate", "e3")], pcid="v2")
    await apply_merges(
        db,
        org_id,
        "features_mentioned",
        [Merge("magnesium_glycinate", "glycinate", 0.92, "same compound")],
        source="auto",
    )
    counts = {
        r["slug"]: r["count"]
        for r in await db.fetch("SELECT slug, count FROM tag_vocab WHERE org_id=$1", org_id)
    }
    # two runs carry the canonical after the rewrite; the first run carried both members
    # and must not be counted twice
    assert counts == {"glycinate": 2, "magnesium_glycinate": 0}


async def test_apply_merges_rejects_a_canonical_outside_the_vocabulary(db, org_id):
    await _seed_vocab(db, org_id, "concept_tags", {"gummy": 1})
    with pytest.raises(ValueError, match="not in the vocabulary"):
        await apply_merges(
            db, org_id, "concept_tags", [Merge("gummy", "nope", 0.9, "r")], source="auto"
        )
    assert (
        await db.fetchval(
            "SELECT canonical_slug FROM tag_vocab WHERE org_id=$1 AND slug='gummy'", org_id
        )
        == "gummy"
    )
    assert await db.fetchval("SELECT count(*) FROM tag_vocab_merge WHERE org_id=$1", org_id) == 0


async def test_approving_a_review_twice_raises_and_leaves_one_log_row(db, org_id):
    await _seed_vocab(db, org_id, "concept_tags", {"gummy": 2, "easy_gummy_format": 4})
    await _open_rows(db, org_id, "concept_tags", [("gummy", "e")])
    await record_review(
        db, org_id, "concept_tags", [Merge("gummy", "easy_gummy_format", 0.74, "maybe")]
    )
    (pending,) = await list_review(db, org_id)
    await decide_review(db, pending["id"], approve=True)
    with pytest.raises(ValueError, match="no pending review merge"):
        await decide_review(db, pending["id"], approve=True)
    rows = await db.fetch(
        "SELECT id, status, source FROM tag_vocab_merge "
        "WHERE org_id=$1 AND dimension='concept_tags' AND slug='gummy' "
        "AND into_slug='easy_gummy_format'",
        org_id,
    )
    assert [(r["id"], r["status"], r["source"]) for r in rows] == [
        (pending["id"], "applied", "manual")
    ]


async def test_revert_stops_the_pair_from_being_reproposed(db, org_id):
    await _seed_vocab(db, org_id, "concept_tags", {"gummy": 2, "easy_gummy_format": 4})
    await _open_rows(db, org_id, "concept_tags", [("gummy", "e"), ("easy_gummy_format", "f")])
    await vocab_merge.embed_missing(db, FakeEmbedder(), org_id, "concept_tags", MODEL)
    before = await vocab_merge.candidate_pairs(db, org_id, "concept_tags", MODEL, floor=-1.0)
    assert [(p.a, p.b) for p in before] == [("easy_gummy_format", "gummy")]
    await apply_merges(
        db, org_id, "concept_tags", [Merge("gummy", "easy_gummy_format", 0.9, "r")], source="auto"
    )
    await revert(db, org_id, "concept_tags", "gummy")
    assert await vocab_merge.candidate_pairs(db, org_id, "concept_tags", MODEL, floor=-1.0) == []
    row = await db.fetchrow(
        "SELECT reason, source, into_slug FROM tag_vocab_merge "
        "WHERE org_id=$1 AND status='rejected' AND slug='gummy'",
        org_id,
    )
    assert tuple(row) == ("reverted by operator", "manual", "easy_gummy_format")


async def test_record_rejections_skips_unanswered_verdicts(db, org_id):
    """A pair the judge never answered must not become a permanent rejection."""
    await _seed_vocab(db, org_id, "concept_tags", {"gummy": 1, "melatonin": 1, "sleepy": 1})
    await record_rejections(
        db,
        org_id,
        "concept_tags",
        [
            _v("gummy", "melatonin", 0.7, False),
            Verdict(Pair("gummy", "sleepy", 0.8), False, "no verdict returned", answered=False),
        ],
    )
    rows = await db.fetch(
        "SELECT slug, into_slug FROM tag_vocab_merge WHERE org_id=$1 ORDER BY into_slug", org_id
    )
    assert [(r["slug"], r["into_slug"]) for r in rows] == [("gummy", "melatonin")]


async def test_decide_review_follows_a_target_that_was_merged_away(db, org_id):
    """A review row queued before its target was itself merged must land on the current
    canonical, not resurrect a merged-away slug as a merge target."""
    await _seed_vocab(db, org_id, "concept_tags", {"x_slug": 1, "y_slug": 2, "z_slug": 3})
    await _open_rows(db, org_id, "concept_tags", [("x_slug", "e"), ("y_slug", "f")])
    await _open_rows(db, org_id, "concept_tags", [("z_slug", "g")], pcid="v2")
    await record_review(db, org_id, "concept_tags", [Merge("x_slug", "y_slug", 0.75, "maybe")])
    await apply_merges(
        db, org_id, "concept_tags", [Merge("y_slug", "z_slug", 0.9, "same")], source="auto"
    )
    (pending,) = await list_review(db, org_id)
    await decide_review(db, pending["id"], approve=True)
    canon = {
        r["slug"]: r["canonical_slug"]
        for r in await db.fetch(
            "SELECT slug, canonical_slug FROM tag_vocab WHERE org_id=$1", org_id
        )
    }
    assert canon == {"x_slug": "z_slug", "y_slug": "z_slug", "z_slug": "z_slug"}
    values = sorted(
        r["value"]
        for r in await db.fetch(
            "SELECT value FROM tag WHERE org_id=$1 AND dimension='concept_tags'", org_id
        )
    )
    assert values == ["z_slug", "z_slug", "z_slug"]
    row = await db.fetchrow(
        "SELECT into_slug, status FROM tag_vocab_merge WHERE id=$1", pending["id"]
    )
    assert (row["into_slug"], row["status"]) == ("z_slug", "applied")
    # two runs carry the canonical; the run that carried both x and y counts once
    assert await vocab.hints(db, org_id, "concept_tags", 10) == [("z_slug", 2)]


async def test_decide_review_rejects_when_the_target_merged_into_this_slug(db, org_id):
    await _seed_vocab(db, org_id, "concept_tags", {"gummy": 2, "easy_gummy_format": 4})
    await _open_rows(db, org_id, "concept_tags", [("gummy", "e"), ("easy_gummy_format", "f")])
    await record_review(
        db, org_id, "concept_tags", [Merge("gummy", "easy_gummy_format", 0.74, "maybe")]
    )
    # the reverse merge wins the race: easy_gummy_format is now pointed at gummy
    await apply_merges(
        db, org_id, "concept_tags", [Merge("easy_gummy_format", "gummy", 0.9, "r")], source="auto"
    )
    (pending,) = await list_review(db, org_id)
    await decide_review(db, pending["id"], approve=True)
    row = await db.fetchrow("SELECT status, reason FROM tag_vocab_merge WHERE id=$1", pending["id"])
    assert (row["status"], row["reason"]) == ("rejected", "target merged into this slug")
    assert (
        await db.fetchval(
            "SELECT canonical_slug FROM tag_vocab WHERE org_id=$1 AND slug='gummy'", org_id
        )
        == "gummy"
    )


async def test_apply_merges_rejects_a_non_canonical_target(db, org_id):
    await _seed_vocab(db, org_id, "concept_tags", {"gummy": 2, "easy_gummy_format": 4, "gum": 1})
    await apply_merges(
        db, org_id, "concept_tags", [Merge("gum", "easy_gummy_format", 0.9, "r")], source="auto"
    )
    with pytest.raises(ValueError, match="is not canonical"):
        await apply_merges(
            db, org_id, "concept_tags", [Merge("gummy", "gum", 0.9, "r")], source="auto"
        )


async def test_revert_rejects_an_unknown_slug(db, org_id):
    with pytest.raises(ValueError, match="no vocab row for 'nope'"):
        await revert(db, org_id, "concept_tags", "nope")


async def test_revert_is_a_no_op_for_a_slug_that_was_never_merged(db, org_id):
    await _seed_vocab(db, org_id, "concept_tags", {"gummy": 7})
    await revert(db, org_id, "concept_tags", "gummy")
    assert (
        await db.fetchval("SELECT count FROM tag_vocab WHERE org_id=$1 AND slug='gummy'", org_id)
        == 7
    )
    assert await db.fetchval("SELECT count(*) FROM tag_vocab_merge WHERE org_id=$1", org_id) == 0
