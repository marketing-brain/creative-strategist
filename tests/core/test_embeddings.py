from types import SimpleNamespace

import pytest

from cs.core.embeddings import FakeEmbedder, GatewayEmbedder, cosine


class _FakeEmbeddings:
    def __init__(self):
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        n = len(kwargs["input"])
        return SimpleNamespace(
            data=[SimpleNamespace(embedding=[float(i), 1.0]) for i in range(n)],
            usage=SimpleNamespace(prompt_tokens=n * 5, total_tokens=n * 5),
        )


async def test_gateway_embedder_batches_and_passes_dimensions(settings):
    fake = _FakeEmbeddings()
    emb = GatewayEmbedder(settings, client=SimpleNamespace(embeddings=fake))
    vectors = await emb.embed([f"t{i}" for i in range(250)])
    assert len(vectors) == 250 and vectors[0] == [0.0, 1.0] and vectors[249] == [49.0, 1.0]
    assert [len(c["input"]) for c in fake.calls] == [100, 100, 50]
    assert all(c["model"] == settings.embedding_model for c in fake.calls)
    assert all(c["dimensions"] == settings.embedding_dimensions for c in fake.calls)


async def test_gateway_embedder_empty_input_makes_no_call(settings):
    fake = _FakeEmbeddings()
    emb = GatewayEmbedder(settings, client=SimpleNamespace(embeddings=fake))
    assert await emb.embed([]) == [] and fake.calls == []


async def test_fake_embedder_is_deterministic_and_similar_for_similar_text():
    a = await FakeEmbedder().embed(["concept tags: benadryl swap"])
    b = await FakeEmbedder().embed(["concept tags: benadryl swap"])
    assert a == b and len(a[0]) == 1536
    same, other = await FakeEmbedder().embed(["x: glycinate", "x: melatonin"])
    assert cosine(a[0], a[0]) == pytest.approx(1.0)
    assert cosine(same, other) < 0.999
    assert len((await FakeEmbedder(dims=8).embed(["x"]))[0]) == 8


def test_cosine():
    assert cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert cosine([0, 0], [1, 0]) == 0.0


def test_settings_defaults(settings):
    assert settings.embedding_model == "google/gemini-embedding-2"
    assert settings.embedding_dimensions == 1536
