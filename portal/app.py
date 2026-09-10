import asyncio
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from config import Settings
from portal import data, session

settings = Settings()
TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

app = FastAPI()
app.state.pending_error = "none"
app.state.session_ttl_override = None


def _exempt(path: str) -> bool:
    return path == "/" or path.startswith("/login") or path.startswith("/error/") or path.startswith("/admin/")


@app.middleware("http")
async def session_and_faults(request: Request, call_next):
    pending = app.state.pending_error
    if pending == "slow_load":
        app.state.pending_error = "none"
        await asyncio.sleep(5)
    elif pending == "server_error":
        app.state.pending_error = "none"
        return HTMLResponse("Internal Server Error", status_code=500)

    if not _exempt(request.url.path):
        sid = request.cookies.get("session_id")
        ttl = getattr(app.state, "session_ttl_override", None) or settings.portal_session_ttl
        if not sid or not session.validate_session(sid, ttl):
            return RedirectResponse("/login?expired=1", status_code=303)

    return await call_next(request)


@app.get("/")
async def root():
    return RedirectResponse("/login", status_code=303)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "expired": request.query_params.get("expired") == "1",
            "access_denied": "access_denied" in request.query_params,
        },
    )


@app.post("/login")
async def login_submit(username: str = Form(...), password: str = Form(...)):
    if not username.strip() or not password.strip():
        return RedirectResponse("/login", status_code=303)
    sid = session.create_session(username.strip())
    resp = RedirectResponse("/search", status_code=303)
    resp.set_cookie("session_id", sid)
    return resp


@app.get("/search", response_class=HTMLResponse)
async def search_page(request: Request):
    sid = request.cookies.get("session_id", "")
    sess = session.get_session(sid) or {}
    q = request.query_params.get("q")
    results = data.search_applications(q) if q is not None and q != "" else None
    if q == "":
        results = []
    return templates.TemplateResponse(
        request,
        "search.html",
        {
            "username": sess.get("username", ""),
            "query": q,
            "results": results,
        },
    )


@app.get("/application/{app_id}", response_class=HTMLResponse)
async def application_detail(request: Request, app_id: str):
    app_data = data.get_application(app_id)
    if app_data is None:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"message": f"Application {app_id} not found"},
            status_code=404,
        )
    sid = request.cookies.get("session_id", "")
    sess = session.get_session(sid) or {}
    return templates.TemplateResponse(
        request,
        "detail.html",
        {
            "username": sess.get("username", ""),
            "app": app_data,
        },
    )


@app.get("/error/session-expired", response_class=HTMLResponse)
async def session_expired(request: Request):
    return templates.TemplateResponse(
        request,
        "error.html",
        {"message": "Session Expired"},
    )


@app.post("/admin/expire-session")
async def expire_session(request: Request):
    sid = request.cookies.get("session_id")
    if sid:
        session.delete_session(sid)
    target = request.headers.get("referer") or "/search"
    return RedirectResponse(target, status_code=303)


@app.post("/admin/inject-error")
async def inject_error(error_type: str = Form(...)):
    if error_type in ("slow_load", "server_error", "none"):
        app.state.pending_error = error_type
    return RedirectResponse("/search", status_code=303)


@app.post("/admin/set-ttl")
async def set_ttl(ttl: int = Form(...)):
    """Set session TTL in seconds (for testing)."""
    app.state.session_ttl_override = ttl
    return RedirectResponse("/search", status_code=303)
