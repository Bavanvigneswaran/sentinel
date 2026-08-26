"""FCM HTTP v1 transport.

Kept out of notify.py for the same reason app/reports/mailer.py is kept out of
report_service.py: the dispatch logic should read as "which channels does this
user want", not as OAuth2 and JSON envelopes.

Unconfigured is a no-op, logged and not an error — the same posture SMTP and
VAPID already take in this package. Nothing here ever raises into the
evaluator sweep.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import service_account

from app.config import get_settings

logger = logging.getLogger(__name__)

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
FCM_ENDPOINT = "https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"

#: Must match CHANNEL_ID in frontend/mobile/src/lib/push.ts. Android silently drops a
#: message naming a channel the app never created, so these two strings are one
#: contract in two files.
ANDROID_CHANNEL_ID = "alerts"

REQUEST_TIMEOUT_SECONDS = 10.0

#: FCM's own words for "this token is dead, stop sending to it". Anything else
#: is treated as transient and the token is kept — the equivalent of web push's
#: 404/410-means-delete, everything-else-means-retry-next-time rule.
_DEAD_TOKEN_STATUSES = frozenset({"UNREGISTERED", "INVALID_ARGUMENT", "NOT_FOUND"})

#: Cached across sends. google-auth refreshes it in place shortly before expiry
#: rather than minting a new assertion per notification.
_credentials: service_account.Credentials | None = None


def is_configured() -> bool:
    settings = get_settings()
    return bool(settings.fcm_project_id and settings.fcm_service_account_file)


def _load_credentials() -> service_account.Credentials:
    global _credentials
    if _credentials is None:
        settings = get_settings()
        _credentials = service_account.Credentials.from_service_account_file(
            settings.fcm_service_account_file, scopes=[FCM_SCOPE]
        )
    return _credentials


def _refresh_and_read_token() -> str:
    """Blocking: google-auth's only transport is `requests`. Always called
    through asyncio.to_thread — CLAUDE.md's "nothing blocking on the event
    loop" applies to a notification dispatch exactly as much as to a request
    handler, and this is the same treatment pywebpush already gets in
    notify.py."""
    credentials = _load_credentials()
    if not credentials.valid:
        credentials.refresh(GoogleAuthRequest())
    return str(credentials.token)


def reset_credentials_cache() -> None:
    """Test seam. Settings are lru_cached and so is the credential built from
    them; a test that swaps the service-account file needs both cleared."""
    global _credentials
    _credentials = None


def build_message(
    token: str, *, title: str, body: str, data: dict[str, str]
) -> dict[str, Any]:
    """The FCM v1 envelope for one device.

    Both a `notification` block and a `data` block are sent on purpose. The
    notification block is what Android displays when the app is backgrounded or
    dead (the app is not running to render anything itself); the data block is
    what survives to the tap handler so frontend/mobile/src/navigation/linking.ts knows
    where to go. Sending only one of the two loses one of those behaviours.
    """
    return {
        "message": {
            "token": token,
            "notification": {"title": title, "body": body},
            # FCM requires every data value to be a string; callers pass
            # already-stringified values rather than relying on coercion here.
            "data": data,
            "android": {
                "priority": "high",
                "notification": {"channel_id": ANDROID_CHANNEL_ID},
            },
        }
    }


def _dead_token(payload: dict[str, Any]) -> bool:
    error = payload.get("error") or {}
    if error.get("status") in _DEAD_TOKEN_STATUSES:
        return True
    for detail in error.get("details") or []:
        if detail.get("errorCode") in _DEAD_TOKEN_STATUSES:
            return True
    return False


async def send_to_tokens(
    tokens: list[str], *, title: str, body: str, data: dict[str, str]
) -> list[str]:
    """Send one alert to every registered device.

    Returns the tokens FCM reported as permanently dead, for the caller to
    delete. Every send is independently try/excepted: one phone that has
    uninstalled the app must not stop the notification reaching the other.

    No batching. FCM's `sendEach` batch endpoint was retired, and a user has a
    phone or two, not thousands of them.
    """
    if not tokens:
        return []
    if not is_configured():
        logger.info("fcm suppressed (fcm_project_id / fcm_service_account_file not configured)")
        return []

    settings = get_settings()
    try:
        access_token = await asyncio.to_thread(_refresh_and_read_token)
    except Exception:
        # A bad or missing key file must not take the evaluator down with it.
        logger.warning("fcm credentials unavailable; skipping dispatch", exc_info=True)
        return []

    url = FCM_ENDPOINT.format(project_id=settings.fcm_project_id)
    headers = {"Authorization": f"Bearer {access_token}"}
    dead: list[str] = []

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        for token in tokens:
            message = build_message(token, title=title, body=body, data=data)
            try:
                response = await client.post(url, headers=headers, json=message)
            except Exception:
                logger.warning("fcm dispatch failed token=%s", _redact(token), exc_info=True)
                continue

            if response.status_code < 400:
                continue

            try:
                payload = response.json()
            except ValueError:
                payload = {}

            if _dead_token(payload):
                # Routine cleanup, not an error: the app was uninstalled or the
                # token rotated. Same handling as a 404/410 from a web push
                # endpoint.
                dead.append(token)
            else:
                logger.warning(
                    "fcm dispatch rejected token=%s status=%s body=%s",
                    _redact(token),
                    response.status_code,
                    response.text[:200],
                )

    return dead


def _redact(token: str) -> str:
    """A registration token is a bearer credential for delivering to that
    device; the log gets enough to correlate, not enough to reuse."""
    return f"{token[:8]}…" if len(token) > 8 else "…"
