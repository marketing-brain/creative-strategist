import pytest

from cs.core.blobs import LocalBlobStore, S3BlobStore, blob_store_from_settings, sha256_hex


async def test_local_put_get_exists_url(tmp_path):
    store = LocalBlobStore(tmp_path, public_base_url="http://localhost:8000")
    key = "org1/creatives/abc/original.png"
    assert not await store.exists(key)
    await store.put(key, b"hello", "image/png")
    assert await store.exists(key)
    assert await store.get(key) == b"hello"
    assert await store.url_for(key) == "http://localhost:8000/blobs/org1/creatives/abc/original.png"
    assert sha256_hex(b"hello").startswith("2cf24dba")


async def test_local_exists_is_false_for_directories(tmp_path):
    store = LocalBlobStore(tmp_path)
    await store.put("org1/creatives/abc.png", b"x", "image/png")
    assert await store.exists("org1/creatives") is False
    assert await store.exists("org1/creatives/abc.png") is True


async def test_local_rejects_path_traversal(tmp_path):
    store = LocalBlobStore(tmp_path)

    with pytest.raises(ValueError):
        await store.put("../escape", b"x", "text/plain")


def test_s3_credentials_are_passed_explicitly_not_left_to_boto3s_env_lookup(monkeypatch, settings):
    """Regression test: S3BlobStore used to construct boto3.client() with no credential
    kwargs at all, relying on boto3 finding AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY in
    os.environ -- but Settings loads .env into its own object via pydantic-settings,
    which does not export those values back into the process environment, so this only
    ever worked if the shell that launched the process happened to `source .env` itself
    first. Confirmed live: this caused a real NoCredentialsError with valid credentials
    sitting unused in .env the whole time."""
    settings.blob_backend = "s3"
    settings.s3_bucket = "my-bucket"
    settings.s3_endpoint_url = "https://example.supabase.co/storage/v1/s3"
    settings.aws_access_key_id = "AKIA_TEST"
    settings.aws_secret_access_key = "secret_test"
    settings.aws_default_region = "us-west-2"

    captured = {}

    class FakeBoto3:
        @staticmethod
        def client(service, **kwargs):
            captured["service"] = service
            captured.update(kwargs)
            return object()

    import sys

    monkeypatch.setitem(sys.modules, "boto3", FakeBoto3)

    blob_store_from_settings(settings)

    assert captured["service"] == "s3"
    assert captured["aws_access_key_id"] == "AKIA_TEST"
    assert captured["aws_secret_access_key"] == "secret_test"
    assert captured["region_name"] == "us-west-2"
    assert captured["endpoint_url"] == "https://example.supabase.co/storage/v1/s3"


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
