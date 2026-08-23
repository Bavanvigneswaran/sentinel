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

from app.api.deps import CurrentUser
from app.schemas.downloads import AgentBuildOut, AgentDownloadsOut
from app.services import download_service as svc

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


@router.get("/downloads/agent/{filename}")
async def download_agent_build(filename: str, user: CurrentUser) -> FileResponse:
    catalog = await svc.load_catalog()
    path = svc.resolve_artifact(catalog, filename)
    if path is None:
        # One response for "not in the manifest", "manifest missing", and "the
        # file is gone from disk". None of them are distinguishable to a caller
        # and none of them should be.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such build")

    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=filename,
        # The binary is immutable under this name — a rebuild produces a new
        # version in the filename — but it is served behind auth, so private.
        headers={"Cache-Control": "private, max-age=3600"},
    )
