"""Auth endpoints.

Mounted at /auth with NO /api prefix: the Vite dev proxy rewrites /api/* to /*,
so the browser calls /api/auth/login and this serves /auth/login. A production
reverse proxy must strip /api the same way.

Errors use FastAPI's native {"detail": ...}. Note for clients: `detail` is a
string for everything except 422, where it is an array of objects.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)

from app.api.csrf import enforce_same_site
from app.api.deps import CurrentUser, UnscopedSession
from app.api.ratelimit import (
    forgot_password_email_limit,
    forgot_password_ip_limit,
    login_email_limit,
    login_ip_limit,
    logout_limit,
    refresh_limit,
    reset_password_limit,
    signup_limit,
)
from app.config import get_settings
from app.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    ResetPasswordRequest,
    SessionResponse,
    SignupRequest,
    UserOut,
)
from app.security.cookies import clear_refresh_cookie, set_refresh_cookie
from app.services import auth_service, password_reset
from app.services.auth_service import (
    EmailAlreadyRegistered,
    InvalidCredentials,
    InvalidRefreshToken,
    InvalidResetToken,
    IssuedSession,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# Identical for a wrong password, an unknown email, and a deactivated account.
# The unknown-email path also burns an equivalent argon2 verification, so timing
# does not distinguish them either.
INVALID_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
)
INVALID_REFRESH = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired session"
)
# 400, not 401: there is no credential being re-presented here and nothing for
# a client to retry with, so a 401 would send it into the re-login loop the
# same status code was chosen to avoid on /auth/refresh's CSRF refusal.
# Identical for expired, already-spent, and never-existed, which are the same
# fact from the user's side — ask for another link.
INVALID_RESET = HTTPException(
    status_code=status.HTTP_400_BAD_REQUEST,
    detail="This reset link is invalid or has expired. Request a new one.",
)


def _client_context(request: Request) -> dict:
    return {
        "user_agent": request.headers.get("user-agent"),
        "ip": request.client.host if request.client else None,
    }


def _session_body(issued: IssuedSession, response: Response) -> SessionResponse:
    set_refresh_cookie(response, issued.refresh_secret)
    return SessionResponse(
        access_token=issued.access_token,
        expires_in=issued.expires_in,
        user=UserOut.model_validate(issued.user),
    )


@router.post(
    "/signup",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(signup_limit())],
)
async def signup(
    payload: SignupRequest,
    request: Request,
    response: Response,
    session: UnscopedSession,
) -> SessionResponse:
    """Create an account and log in.

    Signing up logs you straight in — bouncing to a login form would be a UX
    regression for no security gain.
    """
    try:
        user = await auth_service.signup(
            session,
            email=payload.email,
            password=payload.password,
            display_name=payload.display_name,
        )
    except EmailAlreadyRegistered as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        ) from exc

    issued = await auth_service.issue_session(session, user, **_client_context(request))
    return _session_body(issued, response)


@router.post(
    "/login",
    response_model=SessionResponse,
    dependencies=[Depends(login_ip_limit()), Depends(login_email_limit())],
)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: UnscopedSession,
) -> SessionResponse:
    try:
        user = await auth_service.authenticate(
            session, email=payload.email, password=payload.password
        )
    except InvalidCredentials as exc:
        raise INVALID_CREDENTIALS from exc

    issued = await auth_service.issue_session(session, user, **_client_context(request))
    return _session_body(issued, response)


@router.post(
    "/refresh",
    response_model=SessionResponse,
    dependencies=[Depends(refresh_limit()), Depends(enforce_same_site)],
)
async def refresh(
    request: Request,
    response: Response,
    session: UnscopedSession,
) -> SessionResponse:
    """Rotate the refresh cookie and mint a new access token.

    Replaying an already-exchanged token revokes the entire family. Clients MUST
    single-flight this call: two concurrent refreshes with the same cookie are
    indistinguishable from a stolen token.
    """
    settings = get_settings()
    presented = request.cookies.get(settings.refresh_cookie_name)
    if not presented:
        raise INVALID_REFRESH

    try:
        issued = await auth_service.rotate_refresh(session, presented, **_client_context(request))
    except InvalidRefreshToken as exc:
        # Clear the dead cookie so the client stops replaying it.
        clear_refresh_cookie(response)
        raise INVALID_REFRESH from exc

    return _session_body(issued, response)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(logout_limit()), Depends(enforce_same_site)],
)
async def logout(request: Request, response: Response, session: UnscopedSession) -> Response:
    """Always 204, even with no cookie or an unknown one.

    A logout that can fail is a bug. Revokes the whole family, not just the
    presented token.
    """
    settings = get_settings()
    await auth_service.logout(session, request.cookies.get(settings.refresh_cookie_name))
    clear_refresh_cookie(response)
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=dict(response.headers))


def _reset_base_url(request: Request) -> str:
    """Where the emailed link should point.

    Falls back to the origin the request arrived on, which is correct for
    `make serve` — console and API on one port — and behind the Funnel
    `--proxy-headers` makes that the public https:// front door rather than
    the loopback bind. A split dev stack needs the explicit setting.
    """
    configured = get_settings().password_reset_base_url
    return configured.rstrip("/") if configured else str(request.base_url).rstrip("/")


@router.post(
    "/forgot-password",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(forgot_password_ip_limit()), Depends(forgot_password_email_limit())],
)
async def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    background: BackgroundTasks,
    session: UnscopedSession,
) -> Response:
    """Email a reset link. Always 202, whether or not the address has an account.

    The response must not distinguish the two, or this becomes an
    account-enumeration oracle — the same reasoning that makes /auth/login
    answer identically for a wrong password and an unknown email.

    Sending is queued as a background task rather than awaited, and that is
    part of the same guarantee rather than a performance choice: an SMTP
    conversation takes a second or more, so awaiting it would make the
    known-address path visibly slower than the unknown one and time the
    answer the status code refuses to give. It also means a mail failure
    cannot be reported here — it is logged, and the user sees the same
    "check your inbox" either way.
    """
    issued = await auth_service.begin_password_reset(session, email=payload.email)
    if issued is not None:
        user, token = issued
        background.add_task(
            password_reset.send_reset_email,
            user.email,
            password_reset.build_reset_url(_reset_base_url(request), token),
        )
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post(
    "/reset-password",
    response_model=SessionResponse,
    dependencies=[Depends(reset_password_limit())],
)
async def reset_password(
    payload: ResetPasswordRequest,
    request: Request,
    response: Response,
    session: UnscopedSession,
) -> SessionResponse:
    """Spend a reset link and sign the user in on the new password.

    Signing them straight in mirrors /auth/signup: they have just proved
    control of the account's mailbox and chosen a password, so bouncing them
    to a login form to type it again is a UX regression for no security gain.

    The session this issues is the only one that survives — every refresh
    token the account had is revoked, and `password_changed_at` invalidates
    every access token issued before now, so a thief holding a live session
    loses it here. That is the point of a reset.
    """
    try:
        user = await auth_service.complete_password_reset(
            session, token=payload.token, password=payload.password
        )
    except InvalidResetToken as exc:
        raise INVALID_RESET from exc

    issued = await auth_service.issue_session(session, user, **_client_context(request))
    return _session_body(issued, response)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


__all__ = ["router", "Annotated"]
