from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import AnyHttpUrl, Field, PositiveInt
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ADMETLAB_",
        env_file=(".env", ".env.local"),
        extra="ignore",
    )

    base_url: AnyHttpUrl = Field(
        default="https://admetlab3.scbdd.com", description="Base URL for ADMETlab 3.0 API"
    )
    timeout_seconds: PositiveInt = Field(
        default=30, description="HTTP request timeout in seconds"
    )
    retry_attempts: PositiveInt = Field(default=3, description="Retry attempts on 5xx/429")
    retry_backoff: float = Field(
        default=0.5, description="Initial backoff (seconds) for exponential retries"
    )
    rps_limit: PositiveInt = Field(
        default=5, description="Client-side rate limit (requests per second)"
    )
    batch_size: PositiveInt = Field(
        default=1000, description="Maximum SMILES per request before chunking"
    )
    feature_default: bool = Field(
        default=False, description="Default feature flag for /api/admet requests"
    )
    uncertain_default: bool = Field(
        default=False, description="Default uncertain flag for /api/admet requests"
    )
    admet_endpoint: str = Field(
        default="/api/admet",
        description="Primary relative path for ADMET prediction endpoint.",
    )
    admet_fallback_endpoints: list[str] = Field(
        default_factory=lambda: ["/api/single/admet"],
        description="Fallback relative endpoints for ADMET predictions (tried in order when primary fails).",
    )
    api_key: Optional[str] = Field(
        default=None, description="Optional API key header (reserved for future use)"
    )
    log_level: str = Field(default="INFO", description="Application log level")


@lru_cache
def get_settings() -> Settings:
    return Settings()
