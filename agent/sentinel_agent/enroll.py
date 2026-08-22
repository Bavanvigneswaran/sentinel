"""Exchange a one-time enrollment code for a long-lived agent token."""

from __future__ import annotations

import logging
import socket

import httpx

from sentinel_agent.config import AgentConfig

logger = logging.getLogger(__name__)


class EnrollmentError(Exception):
    pass


def enroll(config: AgentConfig, code: str, device_name: str | None = None) -> AgentConfig:
    """Enrol this machine and persist the returned token.

    The user's password is never involved: the one-time code is the only
    credential, and what comes back is an opaque token scoped to this device
    alone and revocable from the UI.
    """
    name = device_name or socket.gethostname()

    try:
        response = httpx.post(
            config.enroll_url,
            json={"code": code, "device_name": name, "platform": "desktop"},
            timeout=15,
        )
    except httpx.HTTPError as exc:
        raise EnrollmentError(f"could not reach {config.enroll_url}: {exc}") from exc

    if response.status_code == 400:
        raise EnrollmentError(
            "the enrollment code was rejected — it may be mistyped, expired, or already used."
        )
    if response.status_code == 429:
        raise EnrollmentError("too many enrollment attempts; wait a while and try again.")
    if response.status_code >= 400:
        raise EnrollmentError(f"enrollment failed ({response.status_code}): {response.text}")

    payload = response.json()
    config.agent_token = payload["agent_token"]
    config.device_id = payload["device_id"]
    config.save()

    logger.info("enrolled as device %s; token written to %s", config.device_id, config.path)
    return config
