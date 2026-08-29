"""Configuration guards from the Phase 1 security review.

These assert that misconfigurations which would silently weaken production are
refused at boot rather than discovered later.
"""

import pytest

from app.config import (
    DEV_APP_DB_PASSWORD,
    DEV_JWT_SECRET,
    PUBLISHED_JWT_SECRETS,
    Settings,
)

SAFE_PROD = {
    "environment": "prod",
    "jwt_secret": "x" * 48,
    "cookie_secure": True,
    "app_db_password": "a-strong-role-password",
    "cookie_samesite": "strict",
    "rate_limit_enabled": True,
}


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **{**SAFE_PROD, **overrides})


def test_a_correctly_configured_production_boots():
    assert _settings().environment == "prod"


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"jwt_secret": DEV_JWT_SECRET}, "jwt_secret"),
        ({"jwt_secret": "too-short"}, "at least 32 bytes"),
        ({"cookie_secure": False}, "cookie_secure"),
        ({"app_db_password": DEV_APP_DB_PASSWORD}, "app_db_password"),
        # SameSite is the only CSRF defence on /auth/refresh and /auth/logout.
        ({"cookie_samesite": "none"}, "CSRF"),
        # .env.example ships this false for good dev reasons (the Vite proxy
        # puts every request in the 127.0.0.1 bucket), so a deployment that
        # copies it and flips ENVIRONMENT=prod would otherwise run with
        # /auth/login and /enroll — the only unauthenticated write in the
        # system — unthrottled, and nothing would say so.
        ({"rate_limit_enabled": False}, "rate_limit_enabled"),
    ],
)
def test_production_refuses_to_start_when_insecure(override, expected):
    with pytest.raises(ValueError, match=expected):
        _settings(**override)


def test_development_tolerates_the_defaults(monkeypatch):
    """The same values must not block local development.

    `JWT_SECRET` is cleared from the environment first: `tests/conftest.py`
    generates one for the session (the committed files carry none), and
    pydantic-settings reads an environment variable even when `_env_file=None`.
    Without this the test would assert the generated value rather than the
    default it exists to check."""
    monkeypatch.delenv("JWT_SECRET", raising=False)
    dev = Settings(_env_file=None, environment="dev")
    assert dev.jwt_secret == DEV_JWT_SECRET
    assert dev.cookie_secure is False


def test_the_role_password_is_constrained_to_a_safe_shape():
    """It is interpolated into CREATE ROLE, which takes no bind parameters."""
    with pytest.raises(ValueError, match="app_db_password"):
        Settings(
            _env_file=None, environment="dev",
            app_db_password="'; DROP DATABASE sentinel; --",
        )


def test_interactive_docs_are_disabled_in_production(monkeypatch):
    """The schema enumerates every endpoint — free reconnaissance in prod.

    Settings are injected rather than set through the environment: get_settings
    is lru_cached, and clearing that cache leaks into every other test that
    holds a reference to the memoized instance.
    """
    import app.main

    monkeypatch.setattr(app.main, "get_settings", lambda: _settings())
    prod_app = app.main.create_app()

    assert prod_app.docs_url is None
    assert prod_app.redoc_url is None
    assert prod_app.openapi_url is None


def test_interactive_docs_are_available_outside_production(monkeypatch):
    import app.main

    monkeypatch.setattr(
        app.main, "get_settings", lambda: Settings(_env_file=None, environment="dev")
    )
    dev_app = app.main.create_app()

    assert dev_app.docs_url == "/docs"
    assert dev_app.openapi_url == "/openapi.json"


@pytest.mark.parametrize("secret", sorted(PUBLISHED_JWT_SECRETS))
def test_every_secret_published_in_this_repository_is_refused(secret):
    """Checking only DEV_JWT_SECRET left a real hole.

    `.env.example` ships `change-me-to-a-long-random-value`, which is 32 bytes,
    so an operator who copied that file and then satisfied every *other*
    production requirement started cleanly while signing every access token
    with a string published in this repository. Anyone who had read it could
    mint a token for any user id they chose. A placeholder is not safer than a
    default for saying "change me" in its own text.
    """
    with pytest.raises(ValueError, match="published in this repository"):
        _settings(jwt_secret=secret)


def test_the_refusal_says_how_to_generate_one():
    """An operator reading this has to know what to do next, not just that
    something is wrong."""
    with pytest.raises(ValueError, match="secrets.token_urlsafe"):
        _settings(jwt_secret=DEV_JWT_SECRET)


def test_a_generated_secret_is_accepted():
    import secrets

    assert _settings(jwt_secret=secrets.token_urlsafe(48)).environment == "prod"


def test_environment_has_no_default_and_must_be_stated():
    """Every check above is gated on `environment == "prod"`. While the field
    defaulted to "dev", a deployment that never set it was never validated —
    it started cleanly with whatever its .env happened to carry, and the guard
    that exists to catch exactly that said nothing.

    A missing value now fails at startup naming the field, which is the one
    failure mode an operator can act on."""
    with pytest.raises(ValueError, match="environment"):
        Settings(_env_file=None)
