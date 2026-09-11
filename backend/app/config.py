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


# Dashboard "KEY = value" pastes land in the value box whole. Strip the prefix so
# a copy/paste slip cannot silently produce a URL or pattern that matches nothing.
#
# Two deliberately narrow forms, because a greedy matcher here corrupts real values:
#   KEY = value      - the key charset excludes ':' and '/', so a URL cannot match
#   KEY: value       - allowed only for an UPPERCASE key, so "https:" never matches
_ENV_PREFIX_RE = re.compile(
    r"^\s*(?:export\s+)?(?:[A-Za-z_][A-Za-z0-9_]*\s*=|(?:[A-Z][A-Z0-9_]*)\s*:)\s*"
)


def _clean_env_value(raw: str | None) -> str | None:
    """Trim quoting and strip an accidental `KEY = ` prefix from a pasted value."""
    if raw is None:
        return None
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1].strip()
    value = _ENV_PREFIX_RE.sub("", value).strip()
    return value or None


def _as_bool(raw: str | None) -> bool:
    return (raw or "").strip().lower() in {"1", "true", "yes", "on"}


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
        cleaned_frontend = _clean_env_value(os.environ.get("FRONTEND_URL"))
        self.frontend_url: str | None = (
            cleaned_frontend.rstrip("/") if cleaned_frontend else None
        )
        self.frontend_preview_regex: str | None = _clean_env_value(
            os.environ.get("FRONTEND_PREVIEW_REGEX")
        )
        # Seed the reference data on first boot when the database is empty.
        # Off by default; exists because the free Render plan has no Shell, which
        # otherwise makes seeding a manual step with a silent failure mode.
        self.auto_seed: bool = _as_bool(os.environ.get("AUTO_SEED"))
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
        except re.error as exc:
            raise ValueError(
                f"FRONTEND_PREVIEW_REGEX is not a valid regular expression: {exc}. "
                f"Received {pattern!r}."
            ) from exc
        if re.search(r"\s", pattern):
            # A browser Origin header never contains whitespace, so a pattern with
            # any can never match. Almost always a paste of "KEY = value".
            raise ValueError(
                "FRONTEND_PREVIEW_REGEX contains whitespace, so it can never match an "
                f"Origin header. Received {pattern!r}. Expected just the pattern, e.g. "
                r"^https://your-app-.*\.vercel\.app$"
            )
        return pattern


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Test helper - forces the next get_settings() call to re-read the environment."""
    get_settings.cache_clear()
