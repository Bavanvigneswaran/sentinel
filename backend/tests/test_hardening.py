"""Configuration guards from the Phase 1 security review.

These assert that misconfigurations which would silently weaken production are
refused at boot rather than discovered later.
"""

import pytest

from app.config import DEV_APP_DB_PASSWORD, DEV_JWT_SECRET, Settings

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


def test_development_tolerates_the_defaults():
    """The same values must not block local development."""
    dev = Settings(_env_file=None, environment="dev")
    assert dev.jwt_secret == DEV_JWT_SECRET
    assert dev.cookie_secure is False


def test_the_role_password_is_constrained_to_a_safe_shape():
    """It is interpolated into CREATE ROLE, which takes no bind parameters."""
    with pytest.raises(ValueError, match="app_db_password"):
        Settings(_env_file=None, app_db_password="'; DROP DATABASE sentinel; --")


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
