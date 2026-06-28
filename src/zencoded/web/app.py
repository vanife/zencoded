"""FastAPI application: authenticated URL submission -> download/encode/publish jobs."""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from starlette.middleware.sessions import SessionMiddleware

from ..config import Settings, get_settings
from ..jobs import registry, run_job
from . import auth
from .auth import require_user, verify_csrf
from .models import JobCreated, JobRequest, JobView, UserView

app = FastAPI(title="zencoded", docs_url="/api/docs", openapi_url="/api/openapi.json")

_settings = get_settings()
app.add_middleware(
    SessionMiddleware,
    secret_key=_settings.session_secret,
    session_cookie="zencoded_session",
    max_age=_settings.session_max_age,
    same_site="lax",
    https_only=_settings.secure_cookies,
)
app.include_router(auth.router)

# Simple in-memory sliding-window rate limiter (per actor).
_rate_hits: dict[str, deque[float]] = defaultdict(deque)


def _check_rate_limit(actor: str, settings: Settings) -> None:
    now = time.monotonic()
    window = settings.rate_limit_window
    hits = _rate_hits[actor]
    while hits and hits[0] <= now - window:
        hits.popleft()
    if len(hits) >= settings.rate_limit_jobs:
        raise HTTPException(status_code=429, detail="rate limit exceeded; slow down")
    hits.append(now)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if get_settings().secure_cookies:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.post("/jobs", response_model=JobCreated, status_code=202)
async def create_job(
    payload: JobRequest,
    background: BackgroundTasks,
    request: Request,
    user: UserView = Depends(require_user),
    settings: Settings = Depends(get_settings),
    _csrf: None = Depends(verify_csrf),
):
    _check_rate_limit(user.login, settings)
    compress = payload.compress or settings.default_compress
    job = registry.create(url=payload.url, compress=compress, actor=user.login)
    background.add_task(run_job, settings, job.id)
    return JobCreated(job_id=job.id, status=job.status.value)


@app.get("/jobs/{job_id}", response_model=JobView)
async def get_job(job_id: str, user: UserView = Depends(require_user)):
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return JobView(**job.to_dict())


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user = request.session.get("user")
    if not user:
        return HTMLResponse(_LOGIN_PAGE)
    return HTMLResponse(_DASHBOARD.replace("{{LOGIN}}", user["login"]))


_LOGIN_PAGE = """<!doctype html><meta charset=utf-8>
<title>zencoded</title>
<h1>zencoded</h1>
<p>Encode a URL's content into a self-extracting script and publish it.</p>
<p><a href="/auth/login">Sign in with GitHub</a></p>
"""

_DASHBOARD = """<!doctype html><meta charset=utf-8>
<title>zencoded</title>
<h1>zencoded</h1>
<p>Signed in as <b>{{LOGIN}}</b> &middot;
  <form method="post" action="/auth/logout" style="display:inline">
    <button>Sign out</button></form></p>
<form id="f">
  <input id="url" size=60 placeholder="https://example.com/file.zip" required>
  <select id="compress">
    <option value="auto">auto</option>
    <option value="always">always</option>
    <option value="never">never</option>
  </select>
  <button>Encode &amp; publish</button>
</form>
<pre id="out"></pre>
<script>
const out = document.getElementById('out');
document.getElementById('f').addEventListener('submit', async (e) => {
  e.preventDefault();
  out.textContent = 'submitting...';
  const r = await fetch('/jobs', {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-Requested-With': 'zencoded'},
    body: JSON.stringify({
      url: document.getElementById('url').value,
      compress: document.getElementById('compress').value,
    }),
  });
  const j = await r.json();
  if (!r.ok) { out.textContent = 'error: ' + (j.detail || r.status); return; }
  poll(j.job_id);
});
async function poll(id) {
  const r = await fetch('/jobs/' + id, {headers: {'X-Requested-With': 'zencoded'}});
  const j = await r.json();
  out.textContent = JSON.stringify(j, null, 2);
  if (j.status === 'queued' || j.status === 'running') setTimeout(() => poll(id), 1000);
}
</script>
"""
