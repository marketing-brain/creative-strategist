from cs.core.blobs import LocalBlobStore, sha256_hex


async def test_local_put_get_exists_url(tmp_path):
    store = LocalBlobStore(tmp_path, public_base_url="http://localhost:8000")
    key = "org1/creatives/abc/original.png"
    assert not await store.exists(key)
    await store.put(key, b"hello", "image/png")
    assert await store.exists(key)
    assert await store.get(key) == b"hello"
    assert await store.url_for(key) == "http://localhost:8000/blobs/org1/creatives/abc/original.png"
    assert sha256_hex(b"hello").startswith("2cf24dba")


async def test_local_rejects_path_traversal(tmp_path):
    store = LocalBlobStore(tmp_path)
    import pytest

    with pytest.raises(ValueError):
        await store.put("../escape", b"x", "text/plain")
