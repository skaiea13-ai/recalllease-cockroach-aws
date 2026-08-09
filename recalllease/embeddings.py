from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Sequence
from typing import ClassVar, Protocol

import boto3


class EmbeddingProvider(Protocol):
    dimensions: int

    def embed(self, text: str) -> list[float]: ...


class DeterministicEmbeddingProvider:
    """Offline-safe feature hashing for tests and the zero-cost cloud path.

    The provider is deliberately deterministic so policy decisions never depend
    on a paid remote model or nondeterministic inference. Its dimensions match
    the CockroachDB vector column used by both supported embedding backends.
    """

    dimensions = 1024
    _token_pattern: ClassVar[re.Pattern[str]] = re.compile(r"[a-z0-9]+")
    _aliases: ClassVar[dict[str, str]] = {
        "post": "publish",
        "posting": "publish",
        "published": "publish",
        "report": "status",
        "update": "status",
        "cancel": "revoke",
        "cancelled": "revoke",
        "revoked": "revoke",
    }

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = [
            self._aliases.get(token, token) for token in self._token_pattern.findall(text.lower())
        ]
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        return _normalize(vector)


class BedrockEmbeddingProvider:
    dimensions = 1024

    def __init__(self, *, region: str, model_id: str) -> None:
        self._client = boto3.client("bedrock-runtime", region_name=region)
        self._model_id = model_id

    def embed(self, text: str) -> list[float]:
        response = self._client.invoke_model(
            modelId=self._model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(
                {
                    "inputText": text[:8_000],
                    "dimensions": self.dimensions,
                    "normalize": True,
                }
            ),
        )
        body = json.loads(response["body"].read())
        embedding = body.get("embedding")
        if not isinstance(embedding, Sequence) or len(embedding) != self.dimensions:
            raise RuntimeError("Bedrock returned an unexpected embedding payload")
        return [float(value) for value in embedding]


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True))


def _normalize(vector: list[float]) -> list[float]:
    magnitude = math.sqrt(sum(value * value for value in vector))
    if magnitude == 0:
        return vector
    return [value / magnitude for value in vector]
