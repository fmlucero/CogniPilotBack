"""Application settings — pydantic-settings reads .env automatically."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # App
    app_name: str = "cognipilot-back"
    app_env: str = "production"  # production | development | test
    log_level: str = "INFO"

    # Database
    database_url: str  # async (asyncpg)
    database_url_sync: str  # sync (psycopg2) — for Alembic

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # JWT — MUST match the cognipilot-remote (Next.js) backend for token compatibility
    jwt_secret: str
    jwt_refresh_secret: str
    jwt_algorithm: str = "HS256"
    access_token_ttl_min: int = 15
    refresh_token_ttl_days: int = 30

    # Cookies (same names as Next.js back: cp_at / cp_rt)
    cookie_secure: bool = False
    cookie_domain: str = ""

    # Firebase Admin (FCM)
    firebase_service_account_json: str = ""

    # CORS
    cors_allowed_origins: str = ""  # comma-separated

    # Observabilidad — Prometheus server URL (para queries desde /api/metrics/timeseries)
    prometheus_url: str = "http://prometheus:9090"

    @field_validator("cors_allowed_origins")
    @classmethod
    def _strip_cors(cls, v: str) -> str:
        return v.strip()

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    @property
    def is_dev(self) -> bool:
        return self.app_env.lower() in {"development", "dev", "local"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Settings singleton. Cached after first call."""
    return Settings()  # type: ignore[call-arg]
