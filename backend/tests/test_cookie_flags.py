"""The refresh cookie's attributes are a security control, so assert the raw
Set-Cookie header rather than whatever httpx's jar decided to keep."""

SIGNUP = "/auth/signup"
CREDS = {"email": "cookie@example.com", "password": "a-perfectly-fine-password"}


def _set_cookie_header(response, name: str) -> str:
    for raw in response.headers.get_list("set-cookie"):
        if raw.startswith(f"{name}="):
            return raw
    raise AssertionError(f"no Set-Cookie for {name!r}: {response.headers.get_list('set-cookie')}")


async def test_refresh_cookie_flags(client, settings):
    r = await client.post(SIGNUP, json=CREDS)
    header = _set_cookie_header(r, settings.refresh_cookie_name)
    lowered = header.lower()

    assert "httponly" in lowered, "must be unreadable from JavaScript"
    assert "samesite=strict" in lowered
    assert "path=/" in lowered, (
        "the Vite proxy strips /api, so a narrower path would never be sent back"
    )
    assert "max-age=" in lowered
    if settings.cookie_secure:
        assert "secure" in lowered


async def test_the_access_token_is_never_placed_in_a_cookie(client):
    r = await client.post(SIGNUP, json=CREDS)
    access_token = r.json()["access_token"]
    for raw in r.headers.get_list("set-cookie"):
        assert access_token not in raw, "an access token in a cookie would need CSRF defences"


async def test_the_refresh_secret_is_never_in_the_response_body(client, settings):
    r = await client.post(SIGNUP, json=CREDS)
    secret = r.cookies[settings.refresh_cookie_name]
    assert secret not in r.text
