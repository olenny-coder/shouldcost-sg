"""Configuration handling: database URLs and dashboard environment values.

A greedy "strip the KEY = prefix" helper once ate the `https:` from a perfectly
good FRONTEND_URL, which would have silently broken CORS in production. These
tests pin both halves of the behaviour: heal the paste, never touch a real value.
"""

from __future__ import annotations

import pytest

from app.config import Settings, _clean_env_value, _normalise_db_url, reset_settings_cache


# --------------------------------------------------------------------------- #
# A real value must survive untouched
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "value",
    [
        "https://frontend-spare-parts1.vercel.app",
        "http://localhost:8000",
        "http://127.0.0.1:5173",
        r"^https://frontend-[a-z0-9]+-spare-parts1\.vercel\.app$",
        r"^https://shouldcost-.*\.vercel\.app$",
        "sqlite:///./shouldcost.db",
        "postgresql://u:p@host/db?sslmode=require",
    ],
)
def test_real_values_are_returned_unchanged(value: str) -> None:
    assert _clean_env_value(value) == value


def test_only_a_trailing_slash_is_trimmed_by_the_url_setter(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("FRONTEND_URL", "https://frontend-spare-parts1.vercel.app/")
    reset_settings_cache()
    try:
        assert Settings().frontend_url == "https://frontend-spare-parts1.vercel.app"
    finally:
        reset_settings_cache()


# --------------------------------------------------------------------------- #
# A pasted assignment is healed
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("pasted", "expected"),
    [
        (
            "FRONTEND_URL=https://frontend-spare-parts1.vercel.app",
            "https://frontend-spare-parts1.vercel.app",
        ),
        (
            "FRONTEND_URL = https://frontend-spare-parts1.vercel.app",
            "https://frontend-spare-parts1.vercel.app",
        ),
        (
            "export FRONTEND_URL = https://frontend-spare-parts1.vercel.app",
            "https://frontend-spare-parts1.vercel.app",
        ),
        (
            "FRONTEND_PREVIEW_REGEX: ^https://frontend-.*\\.vercel\\.app$",
            r"^https://frontend-.*\.vercel\.app$",
        ),
        (
            '"https://frontend-spare-parts1.vercel.app"',
            "https://frontend-spare-parts1.vercel.app",
        ),
    ],
)
def test_a_pasted_key_prefix_is_stripped(pasted: str, expected: str) -> None:
    assert _clean_env_value(pasted) == expected


def test_a_scheme_is_never_mistaken_for_a_key() -> None:
    """The regression: https: must not be treated as KEY: and stripped."""
    for value in ("https://example.com", "http://localhost:8000"):
        assert _clean_env_value(value) == value


# --------------------------------------------------------------------------- #
# End to end through Settings
# --------------------------------------------------------------------------- #
def test_pasted_frontend_values_still_produce_working_cors(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("FRONTEND_URL", "FRONTEND_URL = https://frontend-spare-parts1.vercel.app")
    monkeypatch.setenv(
        "FRONTEND_PREVIEW_REGEX",
        "FRONTEND_PREVIEW_REGEX = ^https://frontend-[a-z0-9]+-spare-parts1\\.vercel\\.app$",
    )
    reset_settings_cache()
    try:
        settings = Settings()
        assert settings.frontend_url == "https://frontend-spare-parts1.vercel.app"
        assert settings.cors_allow_origins() == ["https://frontend-spare-parts1.vercel.app"]
        import re

        regex = re.compile(settings.cors_allow_origin_regex())
        # The stable alias comes from the exact list, the hashed deployment from the pattern.
        assert regex.match("https://frontend-1ro45cmhd-spare-parts1.vercel.app")
        assert not regex.match("https://evil.example.com")
    finally:
        reset_settings_cache()


def test_a_pattern_with_whitespace_fails_loudly(monkeypatch) -> None:
    """A pattern containing whitespace can never match an Origin header."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("FRONTEND_URL", "https://example.vercel.app")
    monkeypatch.setenv("FRONTEND_PREVIEW_REGEX", "not a pattern")
    reset_settings_cache()
    try:
        with pytest.raises(ValueError) as excinfo:
            Settings().cors_allow_origin_regex()
        assert "whitespace" in str(excinfo.value)
    finally:
        reset_settings_cache()


def test_an_invalid_regex_still_raises(monkeypatch) -> None:
    monkeypatch.setenv("FRONTEND_PREVIEW_REGEX", "https://[unclosed")
    reset_settings_cache()
    try:
        with pytest.raises(ValueError) as excinfo:
            Settings().cors_allow_origin_regex()
        assert "not a valid regular expression" in str(excinfo.value)
    finally:
        reset_settings_cache()


def test_an_empty_value_means_unset(monkeypatch) -> None:
    monkeypatch.setenv("FRONTEND_URL", "   ")
    monkeypatch.setenv("FRONTEND_PREVIEW_REGEX", "")
    reset_settings_cache()
    try:
        settings = Settings()
        assert settings.frontend_url is None
        assert settings.cors_allow_origin_regex() is None
    finally:
        reset_settings_cache()


# --------------------------------------------------------------------------- #
# Database URL normalisation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("postgres://u:p@h/db", "postgresql+psycopg2://u:p@h/db"),
        ("postgresql://u:p@h/db", "postgresql+psycopg2://u:p@h/db"),
        (
            "postgres://u:p@h/db?sslmode=require&channel_binding=require",
            "postgresql+psycopg2://u:p@h/db?sslmode=require&channel_binding=require",
        ),
        ("sqlite:///./shouldcost.db", "sqlite:///./shouldcost.db"),
    ],
)
def test_database_urls_are_normalised_and_query_params_survive(raw: str, expected: str) -> None:
    assert _normalise_db_url(raw) == expected
