from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="RECALLLEASE_",
        extra="ignore",
    )

    environment: Literal["local", "test", "production"] = "local"
    backend: Literal["memory", "cockroach"] = "memory"
    embedding_backend: Literal["deterministic", "bedrock"] = "deterministic"
    exposure_mode: Literal["loopback", "aws_iam"] = "loopback"
    database_url: str | None = None
    database_url_parameter: str | None = None
    loopback_capability: SecretStr | None = Field(default=None, min_length=32)
    aws_region: str = "us-east-1"
    bedrock_embedding_model: str = "amazon.titan-embed-text-v2:0"
    receipt_bucket: str | None = None
    cors_origins: str = "http://localhost:8000,http://127.0.0.1:8000"
    public_session_limit_per_hour: int = Field(default=120, ge=1, le=10_000)
    session_use_limit: int = Field(default=16, ge=4, le=100)
    session_ttl_minutes: int = Field(default=120, ge=15, le=1_440)

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def cloud_mode(self) -> bool:
        return self.backend == "cockroach"


@lru_cache
def get_settings() -> Settings:
    return Settings()
