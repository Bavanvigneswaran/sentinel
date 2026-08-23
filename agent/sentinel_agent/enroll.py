"""Exchange a one-time enrollment code for a long-lived agent token."""

from __future__ import annotations

import logging
import socket

import httpx

from sentinel_agent.config import AgentConfig, requires_tls

logger = logging.getLogger(__name__)


class EnrollmentError(Exception):
    pass


def enroll(
    config: AgentConfig,
    code: str,
    device_name: str | None = None,
    *,
    allow_insecure: bool = False,
) -> AgentConfig:
    """Enrol this machine and persist the returned token.

    The user's password is never involved: the one-time code is the only
    credential, and what comes back is an opaque token scoped to this device
    alone and revocable from the UI.

    Refuses plaintext to a remote host. Enrollment is the one exchange that
    carries both the single-use code *and* the long-lived token it becomes, so
    doing it over http:// hands anyone on the path a credential that is valid
    until somebody notices and revokes it. `allow_insecure` exists for a LAN
    test against a server that has no certificate yet, and has to be asked for
    explicitly.
    """
    if requires_tls(config.server_url) and not allow_insecure:
        raise EnrollmentError(
            f"refusing to send an enrollment code to {config.server_url} over an "
            f"unencrypted connection — the agent token would come back in "
            f"cleartext. Use https://, or pass --insecure if you genuinely mean "
            f"to do this on a trusted network."
        )

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
