"""Agent binary downloads.

Authenticated, like every other read in this API. The agent binary is not a
secret, but making it public would add a second unauthenticated route beside
`/enroll` — which the codebase is careful to keep as the only one — for no gain:
you cannot use an agent without an enrollment code, and you cannot mint one
without signing in.

The catalogue and the file share one source of truth. `resolve_artifact()` will
only ever return a path for a filename the catalogue itself lists, so the route
cannot serve something the page did not offer.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from app.api.deps import CREDENTIALS_ERROR, CurrentUser, CurrentUserOrNone
from app.schemas.downloads import AgentBuildOut, AgentDownloadsOut, DownloadTicketOut
from app.services import download_service as svc
from app.services import download_tickets

router = APIRouter(tags=["downloads"])


@router.get("/downloads/agent", response_model=AgentDownloadsOut)
async def list_agent_builds(user: CurrentUser) -> AgentDownloadsOut:
    catalog = await svc.load_catalog()
    return AgentDownloadsOut(
        configured=catalog.configured,
        generated_at=catalog.generated_at,
        builds=[
            AgentBuildOut(
                os=b.os,
                arch=b.arch,
                version=b.version,
                filename=b.filename,
                size_bytes=b.size_bytes,
                sha256=b.sha256,
                signed=b.signed,
                signing=b.signing,
                built_at=b.built_at,
                download_url=svc.download_url(b),
            )
            for b in catalog.builds
        ],
        unavailable_reason=catalog.unavailable_reason,
    )


@router.post("/downloads/agent/{filename}/ticket", response_model=DownloadTicketOut)
async def mint_agent_download_ticket(filename: str, user: CurrentUser) -> DownloadTicketOut:
    """Mint a short-lived credential the download link can carry instead of a
    bearer token, so a plain `<a download>` — and the browser's own
    resumable-download handling — can own the transfer end to end."""
    catalog = await svc.load_catalog()
    if svc.resolve_artifact(catalog, filename) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such build")

    ticket = await download_tickets.mint_download_ticket(filename)
    return DownloadTicketOut(ticket=ticket, expires_in=download_tickets.TICKET_TTL_SECONDS)


@router.get("/downloads/agent/{filename}")
async def download_agent_build(
    filename: str,
    user: CurrentUserOrNone,
    ticket: str | None = None,
) -> FileResponse:
    # A bearer token still works here unchanged (any other authenticated
    # caller). A ticket is the only credential a plain download link can
    # carry, so it is checked as an equally valid alternative rather than a
    # replacement.
    ticket_ok = bool(ticket) and await download_tickets.check_download_ticket(ticket, filename)
    if user is None and not ticket_ok:
        raise CREDENTIALS_ERROR

    catalog = await svc.load_catalog()
    path = svc.resolve_artifact(catalog, filename)
    if path is None:
        # One response for "not in the manifest", "manifest missing", and "the
        # file is gone from disk". None of them are distinguishable to a caller
        # and none of them should be.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such build")

    # resolve_artifact() only returned a path for a filename already present
    # in catalog.builds, so this lookup cannot miss.
    build = next(b for b in catalog.builds if b.filename == filename)
    media_type = (
        "application/vnd.android.package-archive"
        if build.os == "android"
        else "application/octet-stream"
    )

    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        # The binary is immutable under this name — a rebuild produces a new
        # version in the filename — but it is served behind auth, so private.
        headers={"Cache-Control": "private, max-age=3600"},
    )
