from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Protocol

from cs.core.config import Settings


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class BlobStore(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> str: ...
    async def get(self, key: str) -> bytes: ...
    async def exists(self, key: str) -> bool: ...
    async def url_for(self, key: str, expires_seconds: int = 3600) -> str: ...


def _check_key(key: str) -> None:
    if key.startswith("/") or ".." in key.split("/") or key == "":
        raise ValueError(f"invalid blob key: {key!r}")


class LocalBlobStore:
    def __init__(self, root: Path | str, public_base_url: str = ""):
        self.root = Path(root)
        self.public_base_url = public_base_url.rstrip("/")

    def _path(self, key: str) -> Path:
        _check_key(key)
        return self.root / key

    async def put(self, key: str, data: bytes, content_type: str) -> str:
        p = self._path(key)
        await asyncio.to_thread(p.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(p.write_bytes, data)
        return key

    async def get(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._path(key).is_file)

    async def url_for(self, key: str, expires_seconds: int = 3600) -> str:
        _check_key(key)
        return f"{self.public_base_url}/blobs/{key}"


class S3BlobStore:
    def __init__(self, bucket: str, endpoint_url: str | None = None):
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        # Path-style addressing + explicit SigV4: S3-compatible gateways that
        # aren't AWS itself (e.g. Supabase Storage) reject presigned URLs
        # generated with boto3's virtual-hosted-style default -- the request
        # comes back "AccessDenied: Missing signature" even though direct
        # put/get/delete calls (which don't go through this code path) work.
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    async def put(self, key: str, data: bytes, content_type: str) -> str:
        _check_key(key)
        await asyncio.to_thread(
            self.client.put_object, Bucket=self.bucket, Key=key, Body=data, ContentType=content_type
        )
        return key

    async def get(self, key: str) -> bytes:
        _check_key(key)
        obj = await asyncio.to_thread(self.client.get_object, Bucket=self.bucket, Key=key)
        return await asyncio.to_thread(obj["Body"].read)

    async def exists(self, key: str) -> bool:
        _check_key(key)
        try:
            await asyncio.to_thread(self.client.head_object, Bucket=self.bucket, Key=key)
            return True
        except self.client.exceptions.ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    async def url_for(self, key: str, expires_seconds: int = 3600) -> str:
        _check_key(key)
        return await asyncio.to_thread(
            self.client.generate_presigned_url,
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_seconds,
        )


def blob_store_from_settings(settings: Settings) -> BlobStore:
    if settings.blob_backend == "s3":
        return S3BlobStore(settings.s3_bucket, settings.s3_endpoint_url)
    return LocalBlobStore(settings.blob_root, settings.public_base_url)
