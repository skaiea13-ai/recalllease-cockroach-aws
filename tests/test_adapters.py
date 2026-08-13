from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

import recalllease.api as api_module
import recalllease.embeddings as embeddings_module
import recalllease.receipts as receipts_module
import scripts.bootstrap_database as bootstrap_module
from recalllease.api import build_service, create_app
from recalllease.embeddings import BedrockEmbeddingProvider, DeterministicEmbeddingProvider
from recalllease.receipts import S3ReceiptSink
from recalllease.schema import APP_DATABASE, APP_USER
from recalllease.settings import Settings


def production_database_url(**overrides: str | None) -> str:
    parameters: dict[str, str] = {
        "host": "example.invalid",
        "dbname": APP_DATABASE,
        "user": APP_USER,
        "password": hashlib.sha256(b"test-runtime-credential").hexdigest(),
        "sslmode": "verify-full",
    }
    for key, value in overrides.items():
        if value is None:
            parameters.pop(key, None)
        else:
            parameters[key] = value
    return make_conninfo(**parameters)


class FakeBody:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()


class FakeBedrockClient:
    def __init__(self, embedding: list[float]) -> None:
        self.embedding = embedding
        self.calls: list[dict[str, object]] = []

    def invoke_model(self, **kwargs: object) -> dict[str, FakeBody]:
        self.calls.append(kwargs)
        return {"body": FakeBody({"embedding": self.embedding})}


class FakeS3Client:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def put_object(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


class FakeStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.initialized = False

    def initialize(self) -> None:
        self.initialized = True


class FakeParameterStoreClient:
    def __init__(self, value: str) -> None:
        self.value = value
        self.calls: list[dict[str, object]] = []

    def get_parameter(self, **kwargs: object) -> dict[str, dict[str, str]]:
        self.calls.append(kwargs)
        return {"Parameter": {"Value": self.value}}


def test_bedrock_embedding_provider_validates_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeBedrockClient([0.0] * 1024)
    monkeypatch.setattr(embeddings_module.boto3, "client", lambda *args, **kwargs: client)
    provider = BedrockEmbeddingProvider(
        region="us-east-1",
        model_id="amazon.titan-embed-text-v2:0",
    )

    vector = provider.embed("memory " * 2_000)

    assert len(vector) == 1024
    request = client.calls[0]
    assert request["modelId"] == "amazon.titan-embed-text-v2:0"
    assert len(json.loads(str(request["body"]))["inputText"]) == 8_000

    client.embedding = [0.0]
    with pytest.raises(RuntimeError, match="unexpected embedding"):
        provider.embed("too short")


def test_deterministic_embedding_handles_empty_and_mismatched_vectors() -> None:
    provider = DeterministicEmbeddingProvider()

    assert provider.embed("") == [0.0] * 1024
    assert embeddings_module.cosine_similarity([1.0], [1.0, 2.0]) == 0.0


def test_s3_receipt_is_private_content_addressed_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeS3Client()
    monkeypatch.setattr(receipts_module.boto3, "client", lambda *args, **kwargs: client)
    sink = S3ReceiptSink(bucket="private-receipts", region="us-east-1")
    digest = "a" * 64

    key = sink.put(
        payload={"decision": "deny", "digest_sha256": digest},
        digest_sha256=digest,
        created_at=datetime(2026, 8, 9, 12, 0, tzinfo=UTC),
    )

    assert key == f"receipts/2026/08/09/{digest}.json"
    request = client.calls[0]
    assert request["Bucket"] == "private-receipts"
    assert request["ServerSideEncryption"] == "AES256"
    assert request["IfNoneMatch"] == "*"
    assert json.loads(bytes(request["Body"]))["decision"] == "deny"


def test_cloud_service_requires_database_and_selects_cloud_adapters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        build_service(Settings(environment="production", backend="cockroach"))

    with pytest.raises(RuntimeError, match="RECEIPT_BUCKET"):
        build_service(
            Settings(
                environment="production",
                backend="cockroach",
                database_url=production_database_url(),
            )
        )

    invalid_database_urls = (
        production_database_url(user="root"),
        production_database_url(dbname="defaultdb"),
        production_database_url(sslmode="require"),
        production_database_url(password=None),
    )
    for database_url in invalid_database_urls:
        with pytest.raises(RuntimeError, match="Production database URL"):
            build_service(
                Settings(
                    environment="production",
                    backend="cockroach",
                    database_url=database_url,
                    receipt_bucket="private-receipts",
                )
            )

    with pytest.raises(RuntimeError, match="valid PostgreSQL DSN"):
        build_service(
            Settings(
                environment="production",
                backend="cockroach",
                database_url="not-a-dsn",
                receipt_bucket="private-receipts",
            )
        )

    stores: list[FakeStore] = []

    def make_store(dsn: str) -> FakeStore:
        store = FakeStore(dsn)
        stores.append(store)
        return store

    monkeypatch.setattr(api_module, "CockroachStore", make_store)
    monkeypatch.setattr(api_module, "S3ReceiptSink", lambda **kwargs: object())

    active_service = build_service(
        Settings(
            environment="production",
            backend="cockroach",
            database_url=production_database_url(),
            receipt_bucket="private-receipts",
        )
    )

    assert active_service is not None
    assert isinstance(active_service._embeddings, DeterministicEmbeddingProvider)
    assert stores[0].initialized is True
    assert conninfo_to_dict(stores[0].dsn)["user"] == APP_USER

    monkeypatch.setattr(
        api_module,
        "BedrockEmbeddingProvider",
        lambda **kwargs: DeterministicEmbeddingProvider(),
    )
    bedrock_service = build_service(
        Settings(
            environment="production",
            backend="cockroach",
            embedding_backend="bedrock",
            database_url=production_database_url(),
            receipt_bucket="private-receipts",
        )
    )
    assert isinstance(bedrock_service._embeddings, DeterministicEmbeddingProvider)


def test_iam_cloud_service_loads_the_database_url_from_parameter_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = production_database_url()
    parameter_store = FakeParameterStoreClient(database_url)
    monkeypatch.setattr(
        api_module,
        "boto3",
        SimpleNamespace(client=lambda *_args, **_kwargs: parameter_store),
        raising=False,
    )
    stores: list[FakeStore] = []

    def make_store(dsn: str) -> FakeStore:
        store = FakeStore(dsn)
        stores.append(store)
        return store

    monkeypatch.setattr(api_module, "CockroachStore", make_store)
    monkeypatch.setattr(api_module, "S3ReceiptSink", lambda **_kwargs: object())

    active_service = build_service(
        Settings(
            environment="production",
            backend="cockroach",
            exposure_mode="aws_iam",
            database_url_parameter="/recalllease/cockroach-database-url",
            receipt_bucket="private-receipts",
        )
    )

    assert active_service is not None
    assert stores[0].dsn == database_url
    assert parameter_store.calls == [
        {
            "Name": "/recalllease/cockroach-database-url",
            "WithDecryption": True,
        }
    ]


def test_iam_cloud_service_rejects_a_database_url_environment_variable() -> None:
    with pytest.raises(RuntimeError, match="must not be stored"):
        build_service(
            Settings(
                environment="production",
                backend="cockroach",
                exposure_mode="aws_iam",
                database_url=production_database_url(),
                receipt_bucket="private-receipts",
            )
        )


def test_health_reports_cloud_backend_without_connecting() -> None:
    settings = Settings(
        environment="production",
        backend="cockroach",
        exposure_mode="aws_iam",
        database_url_parameter="/recalllease/cockroach-database-url",
    )
    local_service = build_service(Settings(environment="test", backend="memory"))
    app = create_app(settings=settings, service=local_service)

    health_route = next(route for route in app.routes if getattr(route, "path", None) == "/health")
    assert health_route.endpoint() == {
        "status": "ok",
        "environment": "production",
        "memory_backend": "cockroachdb",
        "embedding_backend": "deterministic",
    }


def test_database_bootstrap_requires_verified_tls(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_password = hashlib.sha256(b"bootstrap-test-credential").hexdigest()
    monkeypatch.setenv("RECALLLEASE_APP_PASSWORD", runtime_password)
    monkeypatch.setenv(
        "RECALLLEASE_ADMIN_DATABASE_URL",
        make_conninfo(host="example.invalid", dbname="defaultdb", user="admin"),
    )

    with pytest.raises(RuntimeError, match="sslmode=verify-full"):
        bootstrap_module._read_configuration()

    monkeypatch.setenv(
        "RECALLLEASE_ADMIN_DATABASE_URL",
        make_conninfo(
            host="example.invalid",
            dbname="defaultdb",
            user="admin",
            sslmode="verify-full",
        ),
    )

    _, database_name, password = bootstrap_module._read_configuration()
    assert database_name == APP_DATABASE
    assert password == runtime_password


def test_database_bootstrap_requires_a_separate_administrator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "RECALLLEASE_ADMIN_DATABASE_URL",
        make_conninfo(
            host="example.invalid",
            dbname="defaultdb",
            user=APP_USER,
            sslmode="verify-full",
        ),
    )
    monkeypatch.setenv(
        "RECALLLEASE_APP_PASSWORD",
        hashlib.sha256(b"bootstrap-test-credential").hexdigest(),
    )

    with pytest.raises(RuntimeError, match="separate administrator"):
        bootstrap_module._read_configuration()


def test_database_bootstrap_revokes_console_default_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_password = hashlib.sha256(b"runtime-test-credential").hexdigest()
    bootstrap_password = hashlib.sha256(b"bootstrap-test-credential").hexdigest()
    admin_url = make_conninfo(
        host="example.invalid",
        dbname="defaultdb",
        user="recalllease_bootstrap",
        password=bootstrap_password,
        sslmode="verify-full",
    )
    statements: list[list[str]] = []
    connection_urls: list[str] = []

    class RecordingConnection:
        def __init__(self, connection_url: str) -> None:
            connection_urls.append(connection_url)
            statements.append([])

        def __enter__(self) -> RecordingConnection:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, statement: object) -> None:
            statements[-1].append(repr(statement))

    monkeypatch.setattr(
        bootstrap_module,
        "_read_configuration",
        lambda: (admin_url, APP_DATABASE, runtime_password),
    )
    monkeypatch.setattr(
        bootstrap_module.psycopg,
        "connect",
        lambda connection_url, *, autocommit: RecordingConnection(connection_url),
    )

    bootstrap_module.bootstrap()

    assert len(connection_urls) == 3
    assert conninfo_to_dict(connection_urls[0])["user"] == "recalllease_bootstrap"
    assert conninfo_to_dict(connection_urls[2])["user"] == APP_USER
    create_index = next(
        index
        for index, statement in enumerate(statements[0])
        if "CREATE USER IF NOT EXISTS" in statement
    )
    revoke_index = next(
        index for index, statement in enumerate(statements[0]) if "REVOKE admin FROM" in statement
    )
    password_index = next(
        index for index, statement in enumerate(statements[0]) if "ALTER USER" in statement
    )
    assert create_index < revoke_index < password_index
    assert any("REVOKE ALL ON DATABASE" in statement for statement in statements[1])
