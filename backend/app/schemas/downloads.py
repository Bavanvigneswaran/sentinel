"""Wire schemas for the agent download catalogue."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AgentBuildOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    os: str
    arch: str
    version: str
    filename: str
    size_bytes: int
    sha256: str
    #: False is rendered, never hidden. An unsigned binary means Gatekeeper or
    #: SmartScreen will interrupt the install, and the page says so before the
    #: click rather than leaving the user to meet it alone.
    signed: bool
    signing: str
    built_at: datetime | None
    download_url: str


class DownloadTicketOut(BaseModel):
    """How long the download credential this request just minted will last.

    The credential itself is **not** here: it goes back as an HttpOnly cookie
    (`app/security/cookies.py`), because a query string is the one place a URL
    reliably leaks — access logs, browser history, `Referer`. The page needs
    only the lifetime, so it can re-mint before a backgrounded tab's ticket
    lapses.
    """

    expires_in: int


class AgentDownloadsOut(BaseModel):
    """The catalogue, including the case where it is empty.

    `unavailable_reason` exists so "nothing published yet" is a state the UI can
    render honestly, rather than an empty list it has to guess about.
    """

    configured: bool
    generated_at: datetime | None
    builds: list[AgentBuildOut]
    unavailable_reason: str | None
    #: Echoed back so the page can name the exact command that builds the
    #: version this server expects.
    source_build_command: str = "cd agent && pip install -e '.[build]' && python build/build.py"
