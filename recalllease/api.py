from __future__ import annotations

import secrets
from collections.abc import Callable
from pathlib import Path

import boto3
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.requests import Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from psycopg import ProgrammingError
from psycopg.conninfo import conninfo_to_dict
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from recalllease.embeddings import (
    BedrockEmbeddingProvider,
    DeterministicEmbeddingProvider,
)
from recalllease.models import (
    ActionReceipt,
    ActionRequest,
    DemoSession,
    DemoState,
    MemoryCreate,
    MemoryRecord,
)
from recalllease.receipts import NullReceiptSink, S3ReceiptSink
from recalllease.schema import APP_DATABASE, APP_USER
from recalllease.service import RecallLeaseService
from recalllease.settings import Settings, get_settings
from recalllease.store import (
    CockroachStore,
    InMemoryStore,
    PublicSessionLimitReached,
    SessionError,
)

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
BROWSER_CLIENT_HEADER = "X-RecallLease-Client"
BROWSER_CLIENT_VALUE = "browser-v1"
LOOPBACK_CAPABILITY_HEADER = "X-RecallLease-Loopback-Capability"


def build_service(settings: Settings) -> RecallLeaseService:
    if settings.environment == "production" and not settings.cloud_mode:
        raise RuntimeError(
            "Production requires RECALLLEASE_BACKEND=cockroach; refusing ephemeral memory"
        )
    if settings.cloud_mode:
        database_url = _resolve_database_url(settings)
        if settings.environment == "production":
            _validate_production_cloud_settings(settings, database_url)
        store = CockroachStore(database_url)
        embeddings = (
            BedrockEmbeddingProvider(
                region=settings.aws_region,
                model_id=settings.bedrock_embedding_model,
            )
            if settings.embedding_backend == "bedrock"
            else DeterministicEmbeddingProvider()
        )
        receipt_sink = (
            S3ReceiptSink(bucket=settings.receipt_bucket, region=settings.aws_region)
            if settings.receipt_bucket
            else NullReceiptSink()
        )
    else:
        store = InMemoryStore()
        embeddings = DeterministicEmbeddingProvider()
        receipt_sink = NullReceiptSink()
    store.initialize()
    return RecallLeaseService(
        store=store,
        embeddings=embeddings,
        receipt_sink=receipt_sink,
        hourly_session_limit=settings.public_session_limit_per_hour,
        session_use_limit=settings.session_use_limit,
        session_ttl_minutes=settings.session_ttl_minutes,
    )


def _resolve_database_url(settings: Settings) -> str:
    if settings.environment == "production" and settings.exposure_mode == "aws_iam":
        if settings.database_url:
            raise RuntimeError(
                "RECALLLEASE_DATABASE_URL must not be stored in the Lambda environment"
            )
        if not settings.database_url_parameter:
            raise RuntimeError(
                "RECALLLEASE_DATABASE_URL_PARAMETER is required for the IAM front door"
            )

    if settings.database_url_parameter:
        try:
            response = boto3.client("ssm", region_name=settings.aws_region).get_parameter(
                Name=settings.database_url_parameter,
                WithDecryption=True,
            )
        except Exception as error:
            raise RuntimeError("Unable to load the database URL from Parameter Store") from error
        parameter = response.get("Parameter")
        value = parameter.get("Value") if isinstance(parameter, dict) else None
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError("Parameter Store returned an empty database URL")
        return value

    if settings.database_url:
        return settings.database_url
    raise RuntimeError(
        "RECALLLEASE_DATABASE_URL or RECALLLEASE_DATABASE_URL_PARAMETER is required in cloud mode"
    )


def _validate_production_cloud_settings(settings: Settings, database_url: str) -> None:
    if not settings.receipt_bucket:
        raise RuntimeError("RECALLLEASE_RECEIPT_BUCKET is required in production")

    try:
        connection_info = conninfo_to_dict(database_url)
    except ProgrammingError as error:
        raise RuntimeError("RECALLLEASE_DATABASE_URL is not a valid PostgreSQL DSN") from error

    required_values = {
        "user": APP_USER,
        "dbname": APP_DATABASE,
        "sslmode": "verify-full",
    }
    if any(connection_info.get(key) != value for key, value in required_values.items()):
        raise RuntimeError(
            "Production database URL must use the dedicated recalllease_app login, "
            "recalllease database, and sslmode=verify-full"
        )
    if not connection_info.get("password"):
        raise RuntimeError("Production database URL must include the runtime login password")


def create_app(
    *,
    settings: Settings | None = None,
    service: RecallLeaseService | None = None,
) -> FastAPI:
    active_settings = settings or get_settings()
    requires_loopback_capability = (
        active_settings.cloud_mode and active_settings.exposure_mode == "loopback"
    )
    configured_loopback_capability = (
        active_settings.loopback_capability.get_secret_value()
        if active_settings.loopback_capability is not None
        else None
    )
    if requires_loopback_capability and configured_loopback_capability is None:
        raise RuntimeError(
            "RECALLLEASE_LOOPBACK_CAPABILITY is required for cloud-backed loopback mode"
        )
    active_service = service or build_service(active_settings)
    app = FastAPI(
        title="RecallLease",
        version="0.1.0",
        description="Expiry- and revocation-aware persistent memory for autonomous agents.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=active_settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=[
            "Content-Type",
            "X-Demo-Token",
            BROWSER_CLIENT_HEADER,
            LOOPBACK_CAPABILITY_HEADER,
        ],
    )

    @app.middleware("http")
    async def security_headers(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        origin = request.headers.get("origin")
        cross_site = request.headers.get("sec-fetch-site", "").lower() == "cross-site"
        disallowed_origin = bool(origin and origin not in active_settings.cors_origin_list)
        state_changing_api_request = request.url.path.startswith("/api/") and request.method in {
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
        }
        supplied_loopback_capability = request.headers.get(LOOPBACK_CAPABILITY_HEADER)
        invalid_loopback_capability = (
            requires_loopback_capability
            and request.url.path.startswith("/api/")
            and (
                supplied_loopback_capability is None
                or configured_loopback_capability is None
                or not secrets.compare_digest(
                    supplied_loopback_capability,
                    configured_loopback_capability,
                )
            )
        )
        if invalid_loopback_capability:
            response = JSONResponse(
                status_code=401,
                content={"detail": "Loopback capability required"},
            )
        elif state_changing_api_request and (cross_site or disallowed_origin):
            response = JSONResponse(
                status_code=403,
                content={"detail": "Cross-site request denied"},
            )
        else:
            response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'self'; form-action 'self'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.mount("/assets", StaticFiles(directory=FRONTEND), name="assets")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(FRONTEND / "index.html")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "environment": active_settings.environment,
            "memory_backend": "cockroachdb" if active_settings.cloud_mode else "in-memory",
            "embedding_backend": active_settings.embedding_backend,
        }

    @app.post("/api/demo/sessions", response_model=DemoSession, status_code=201)
    def create_demo_session(
        x_recalllease_client: str = Header(
            alias=BROWSER_CLIENT_HEADER,
            pattern=f"^{BROWSER_CLIENT_VALUE}$",
        ),
    ) -> DemoSession:
        del x_recalllease_client
        try:
            return active_service.create_demo_session()
        except PublicSessionLimitReached as error:
            raise HTTPException(status_code=429, detail=str(error)) from error

    @app.get("/api/demo/sessions/{tenant_id}", response_model=DemoState)
    def get_demo_state(
        tenant_id: str,
        x_demo_token: str = Header(alias="X-Demo-Token", min_length=20),
    ) -> DemoState:
        return _session_call(
            lambda: active_service.get_state(tenant_id=tenant_id, token=x_demo_token)
        )

    @app.post(
        "/api/demo/sessions/{tenant_id}/memories",
        response_model=MemoryRecord,
        status_code=201,
    )
    def add_memory(
        tenant_id: str,
        memory: MemoryCreate,
        x_demo_token: str = Header(alias="X-Demo-Token", min_length=20),
    ) -> MemoryRecord:
        return _session_call(
            lambda: active_service.add_memory(
                tenant_id=tenant_id,
                token=x_demo_token,
                memory=memory,
            )
        )

    @app.post(
        "/api/demo/sessions/{tenant_id}/actions",
        response_model=ActionReceipt,
        status_code=201,
    )
    def evaluate_action(
        tenant_id: str,
        request: ActionRequest,
        x_demo_token: str = Header(alias="X-Demo-Token", min_length=20),
    ) -> ActionReceipt:
        return _session_call(
            lambda: active_service.evaluate_action(
                tenant_id=tenant_id,
                token=x_demo_token,
                request=request,
            )
        )

    return app


def _session_call[T](call: Callable[[], T]) -> T:
    try:
        return call()
    except SessionError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
