"""Central configuration.

Every value is read from an environment variable and every variable has a
documented local-development default. No credential is stored in the repository.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache

DEFAULT_DATABASE_URL = "sqlite:///./shouldcost.db"
DEFAULT_DEV_CORS_ORIGINS = (
    "http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173,http://127.0.0.1:4173"
)


def _normalise_db_url(raw: str) -> str:
    """Normalise a SQLAlchemy database URL.

    Render historically injects the legacy `postgres://` scheme, which
    SQLAlchemy 2.0 no longer recognises. Rewrite it to the psycopg2 dialect so the
    same code path works against SQLite locally and PostgreSQL in production.
    """
    url = raw.strip()
    if url.startswith("postgres://"):
        return "postgresql+psycopg2://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


class Settings:
    """Runtime settings resolved from the process environment."""

    def __init__(self) -> None:
        self.database_url: str = _normalise_db_url(
            os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL
        )
        self.environment: str = (os.environ.get("ENVIRONMENT") or "development").strip().lower()
        self.frontend_url: str | None = (os.environ.get("FRONTEND_URL") or "").strip().rstrip("/") or None
        self.frontend_preview_regex: str | None = (
            os.environ.get("FRONTEND_PREVIEW_REGEX") or ""
        ).strip() or None
        self.dev_cors_origins: list[str] = [
            origin.strip()
            for origin in (os.environ.get("DEV_CORS_ORIGINS") or DEFAULT_DEV_CORS_ORIGINS).split(",")
            if origin.strip()
        ]

    @property
    def is_production(self) -> bool:
        return self.environment in {"production", "prod"}

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def cors_allow_origins(self) -> list[str]:
        """Explicit CORS allowlist. A wildcard is never returned."""
        origins: list[str] = []
        if not self.is_production:
            origins.extend(self.dev_cors_origins)
        if self.frontend_url:
            origins.append(self.frontend_url)
        # De-duplicate while preserving order.
        seen: set[str] = set()
        unique: list[str] = []
        for origin in origins:
            if origin and origin not in seen:
                seen.add(origin)
                unique.append(origin)
        return unique

    def cors_allow_origin_regex(self) -> str | None:
        """Preview-deployment pattern, opted into via FRONTEND_PREVIEW_REGEX."""
        pattern = self.frontend_preview_regex
        if not pattern:
            return None
        try:
            re.compile(pattern)
        except re.error as exc:  # pragma: no cover - misconfiguration guard
            raise ValueError(
                f"FRONTEND_PREVIEW_REGEX is not a valid regular expression: {exc}"
            ) from exc
        return pattern


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Test helper - forces the next get_settings() call to re-read the environment."""
    get_settings.cache_clear()
