from __future__ import annotations

import json
from datetime import datetime
from typing import Protocol

import boto3


class ReceiptSink(Protocol):
    def put(
        self,
        *,
        payload: dict[str, object],
        digest_sha256: str,
        created_at: datetime,
    ) -> str | None: ...


class NullReceiptSink:
    def put(
        self,
        *,
        payload: dict[str, object],
        digest_sha256: str,
        created_at: datetime,
    ) -> None:
        return None


class S3ReceiptSink:
    def __init__(self, *, bucket: str, region: str) -> None:
        self._bucket = bucket
        self._client = boto3.client("s3", region_name=region)

    def put(
        self,
        *,
        payload: dict[str, object],
        digest_sha256: str,
        created_at: datetime,
    ) -> str:
        key = f"receipts/{created_at:%Y/%m/%d}/{digest_sha256}.json"
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
            ServerSideEncryption="AES256",
            IfNoneMatch="*",
            Metadata={"sha256": digest_sha256},
        )
        return key
