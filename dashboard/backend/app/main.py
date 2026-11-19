"""FastAPI entry point: API routes + the built React app, all on one port."""
from __future__ import annotations

import os
import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .bootstrap import bootstrap
from .config import FRONTEND_DIST
from .library import LibError


@asynccontextmanager
async def lifespan(app: FastAPI):
    # IGCM_ROLE: "web" = only serve pages/API (started by app.serve, which runs the engine
    # in its own process); unset = single-process mode (dev / plain `uvicorn app.main:app`)
    # where this process also runs the engine. The engine lease guarantees only one runs.
    role = os.environ.get("IGCM_ROLE", "all")
    if os.environ.get("IGCM_SKIP_INIT") != "1":
        bootstrap()
    from .jobs import runner
    if role == "all":
        runner.start_engine()
    elif role == "web":
        from .procutil import watch_launcher
        watch_launcher(lambda: None)
    yield
    if role == "all":
        runner.stop_engine(timeout=5)


app = FastAPI(title="Insta Chat Media", lifespan=lifespan, docs_url="/api/docs", redoc_url=None,
              openapi_url="/api/openapi.json")


@app.exception_handler(LibError)
async def lib_error(_: Request, exc: LibError):
    return JSONResponse({"detail": str(exc)}, status_code=exc.status)


@app.exception_handler(sqlite3.IntegrityError)
async def integrity_error(_: Request, exc: sqlite3.IntegrityError):
    # e.g. two browser tabs creating the same folder name at the same moment
    return JSONResponse({"detail": "Something with that name was just created. Refresh and try again."},
                        status_code=409)


@app.exception_handler(sqlite3.OperationalError)
async def busy_error(_: Request, exc: sqlite3.OperationalError):
    if "locked" in str(exc).lower() or "busy" in str(exc).lower():
        return JSONResponse({"detail": "The app is busy for a moment. Please try again."}, status_code=503)
    raise exc


@app.middleware("http")
async def guard(request: Request, call_next):
    # CSRF guard: browsers can't add custom headers to cross-site form posts.
    if request.url.path.startswith("/api/") and request.method in ("POST", "PUT", "PATCH", "DELETE"):
        if request.headers.get("x-igcm") != "1":
            return JSONResponse({"detail": "Missing request header."}, status_code=403)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if request.url.path.startswith("/api/") and "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-store"
    return response


from .api_core import public, router as core_router  # noqa: E402
from .api_library import router as library_router  # noqa: E402

app.include_router(public)
app.include_router(core_router)
app.include_router(library_router)


# --------------------------------------------------------------------------- #
# The React app (built into frontend/dist). Unknown paths fall back to index.html.
# --------------------------------------------------------------------------- #
if (FRONTEND_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    candidate = (FRONTEND_DIST / path).resolve()
    if path and candidate.is_file() and FRONTEND_DIST in candidate.parents:
        return FileResponse(candidate)
    index = FRONTEND_DIST / "index.html"
    if index.is_file():
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    return JSONResponse({"detail": "The web app isn't built yet. Run: cd dashboard/frontend && npm install && "
                                   "npm run build"}, status_code=503)
