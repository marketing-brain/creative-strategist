import pytest

from cs.core.blobs import LocalBlobStore, S3BlobStore, sha256_hex


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

    with pytest.raises(ValueError):
        await store.put("../escape", b"x", "text/plain")


async def test_s3_exists_404_returns_false():
    """Test that S3BlobStore.exists() returns False for 404 errors."""
    import botocore.exceptions

    store = S3BlobStore.__new__(S3BlobStore)
    store.bucket = "b"

    class FakeClient:
        class exceptions:
            ClientError = botocore.exceptions.ClientError

        def head_object(self, **kw):
            raise botocore.exceptions.ClientError({"Error": {"Code": "404"}}, "HeadObject")

    store.client = FakeClient()
    assert await store.exists("k") is False


async def test_s3_exists_403_reraises():
    """Test that S3BlobStore.exists() re-raises non-404 errors like 403."""
    import botocore.exceptions

    store = S3BlobStore.__new__(S3BlobStore)
    store.bucket = "b"

    class FakeClient:
        class exceptions:
            ClientError = botocore.exceptions.ClientError

        def head_object(self, **kw):
            raise botocore.exceptions.ClientError({"Error": {"Code": "403"}}, "HeadObject")

    store.client = FakeClient()
    with pytest.raises(botocore.exceptions.ClientError):
        await store.exists("k")
