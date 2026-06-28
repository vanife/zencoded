"""GitHub OAuth login, session handling, allowlist enforcement, and CSRF defense.

Only GitHub logins in ``ZENCODED_OAUTH_ALLOWLIST`` may sign in (deny-by-default). The
authenticated login is stored in a signed session cookie; protected routes depend on
:func:`require_user`. State-changing requests additionally pass :func:`verify_csrf`.
"""

from __future__ import annotations

from urllib.parse import urlparse

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from ..config import Settings, get_settings
from .models import UserView

router = APIRouter()

_oauth: OAuth | None = None


def get_oauth(settings: Settings) -> OAuth:
    """Lazily build the Authlib OAuth registry with the GitHub provider."""
    global _oauth
    if _oauth is None:
        oauth = OAuth()
        oauth.register(
            name="github",
            client_id=settings.github_client_id,
            client_secret=settings.github_client_secret,
            access_token_url="https://github.com/login/oauth/access_token",
            authorize_url="https://github.com/login/oauth/authorize",
            api_base_url="https://api.github.com/",
            client_kwargs={"scope": "read:user"},
        )
        _oauth = oauth
    return _oauth


def require_user(request: Request) -> UserView:
    """Dependency: return the logged-in user or raise 401."""
    user = request.session.get("user")
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication required"
        )
    return UserView(login=user["login"])


def verify_csrf(request: Request, settings: Settings = Depends(get_settings)) -> None:
    """Reject cross-site state-changing requests.

    Cookie auth alone is forgeable by other origins; we additionally require the
    Origin (or Referer) to match our own host and a non-simple custom header, which a
    cross-site HTML form cannot set.
    """
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        raise HTTPException(status_code=403, detail="missing Origin/Referer")
    expected = urlparse(settings.base_url).netloc
    if urlparse(origin).netloc != expected:
        raise HTTPException(status_code=403, detail="cross-origin request rejected")
    if request.headers.get("x-requested-with") != "zencoded":
        raise HTTPException(status_code=403, detail="missing CSRF header")


@router.get("/auth/login")
async def login(request: Request, settings: Settings = Depends(get_settings)):
    if not settings.github_client_id:
        raise HTTPException(status_code=500, detail="GitHub OAuth is not configured")
    oauth = get_oauth(settings)
    redirect_uri = settings.base_url.rstrip("/") + "/auth/callback"
    # Authlib generates and stores an anti-CSRF `state` in the session automatically.
    return await oauth.github.authorize_redirect(request, redirect_uri)


@router.get("/auth/callback")
async def callback(request: Request, settings: Settings = Depends(get_settings)):
    oauth = get_oauth(settings)
    try:
        token = await oauth.github.authorize_access_token(request)
    except OAuthError as exc:
        raise HTTPException(status_code=400, detail=f"OAuth error: {exc.error}") from exc

    resp = await oauth.github.get("user", token=token)
    resp.raise_for_status()
    profile = resp.json()
    login_name = profile.get("login")
    if not login_name or not settings.is_allowed(login_name):
        # Deny-by-default: not on the allowlist.
        request.session.clear()
        raise HTTPException(status_code=403, detail="account not authorized")

    request.session["user"] = {"login": login_name}
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/auth/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
