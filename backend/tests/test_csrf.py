"""The second layer on the two cookie-authenticated routes.

`SameSite=strict` is the primary defence and remains so. This asserts the layer
behind it: a browser that announces a cross-site request is refused, and every
non-browser client — the Python agent, the Kotlin collector, React Native — is
untouched, because none of them sends `Sec-Fetch-Site` at all.
"""

from __future__ import annotations

import pytest

from app.api.csrf import ALLOWED_FETCH_SITES

SIGNUP = "/auth/signup"
CREDS = {"email": "csrf@example.com", "password": "a-perfectly-fine-password"}


async def _session_cookie(client, settings) -> str:
    resp = await client.post(SIGNUP, json=CREDS)
    return resp.cookies[settings.refresh_cookie_name]


@pytest.mark.parametrize("route", ["/auth/refresh", "/auth/logout"])
@pytest.mark.parametrize("site", ["cross-site", "same-site"])
async def test_a_cross_site_browser_request_is_refused(client, settings, route, site):
    """`same-site` is refused as well as `cross-site`: this product is served
    from one origin, so a sibling subdomain making authenticated calls is not a
    shape that should exist."""
    cookie = await _session_cookie(client, settings)

    resp = await client.post(
        route,
        cookies={settings.refresh_cookie_name: cookie},
        headers={"Sec-Fetch-Site": site},
    )
    assert resp.status_code == 403
    assert "Cross-site" in resp.text


@pytest.mark.parametrize("route", ["/auth/refresh", "/auth/logout"])
@pytest.mark.parametrize("site", sorted(ALLOWED_FETCH_SITES))
async def test_the_console_calling_its_own_api_is_allowed(client, settings, route, site):
    cookie = await _session_cookie(client, settings)

    resp = await client.post(
        route,
        cookies={settings.refresh_cookie_name: cookie},
        headers={"Sec-Fetch-Site": site},
    )
    assert resp.status_code != 403


@pytest.mark.parametrize("route", ["/auth/refresh", "/auth/logout"])
async def test_a_client_that_sends_no_such_header_is_untouched(client, settings, route):
    """The load-bearing case. Every non-browser client sends nothing here, and
    refusing that would break all of them to guard against a browser too old to
    send it — which `SameSite=strict` still covers. Layered, not doubled."""
    cookie = await _session_cookie(client, settings)

    resp = await client.post(route, cookies={settings.refresh_cookie_name: cookie})
    assert resp.status_code != 403


async def test_the_refusal_is_403_and_not_401(client, settings):
    """The credential may be perfectly valid; it was rejected for where it came
    from. A 401 would imply the session is dead and send a legitimate client
    into a re-login loop."""
    cookie = await _session_cookie(client, settings)

    resp = await client.post(
        "/auth/refresh",
        cookies={settings.refresh_cookie_name: cookie},
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert resp.status_code == 403

    # And the same cookie still works from the right place, so nothing was
    # revoked by the refusal.
    ok = await client.post(
        "/auth/refresh",
        cookies={settings.refresh_cookie_name: cookie},
        headers={"Sec-Fetch-Site": "same-origin"},
    )
    assert ok.status_code == 200


async def test_login_and_signup_are_not_gated(client):
    """They carry no ambient credential — there is nothing to forge with — and
    gating them would break a link from an email or a bookmark."""
    resp = await client.post(
        SIGNUP,
        json={"email": "csrf-open@example.com", "password": "a-perfectly-fine-password"},
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert resp.status_code == 201
