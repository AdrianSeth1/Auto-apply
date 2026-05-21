"""FastAPI application factory for the API and Vue client."""

from __future__ import annotations

import mimetypes
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

WEB_DIR = Path(__file__).parent
PROJECT_ROOT = WEB_DIR.parents[1]
FRONTEND_DIST_DIR = WEB_DIR / "static" / "spa"
FRONTEND_ASSETS_DIR = FRONTEND_DIST_DIR / "assets"
FRONTEND_INDEX = FRONTEND_DIST_DIR / "index.html"
FRONTEND_FAVICON = PROJECT_ROOT / "frontend" / "favicon.svg"
FRONTEND_LOGO = PROJECT_ROOT / "docs" / "logo" / "AutoApply_logo.svg"


def _frontend_html() -> FileResponse | HTMLResponse:
    if FRONTEND_INDEX.exists():
        # The Vue build emits hash-suffixed asset filenames
        # (``index-<hash>.js``) referenced from ``index.html``. The
        # asset bundles are safe to cache aggressively because their
        # URLs change whenever they do, but ``index.html`` itself
        # must never be cached -- otherwise the browser will keep
        # asking for last build's JS file (long since deleted from
        # disk) and the user sees a stale, broken-looking app after
        # every redeploy. This bit us when the loading-state UI was
        # shipped: rebuilds changed the hash but cached index.html
        # still pointed at the previous bundle.
        return FileResponse(
            FRONTEND_INDEX,
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    return HTMLResponse(
        (
            "<h1>Frontend build missing</h1>"
            "<p>Run <code>npm install</code> and <code>npm run build</code> "
            "in <code>frontend/</code>.</p>"
        ),
        status_code=503,
    )


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """FastAPI startup/shutdown plumbing.

    Phase 11.4 wires the :class:`ProviderHealthMonitor` here so the
    Settings page's ``Last verified ...`` line is backed by an actual
    background probe rather than the last manual test timestamp. The
    monitor is opt-out via ``AUTOAPPLY_DISABLE_HEALTH_MONITOR=1`` for
    test environments that don't want a stray asyncio task.
    """
    from src.providers.health import get_monitor  # noqa: PLC0415

    monitor = None
    if os.environ.get("AUTOAPPLY_DISABLE_HEALTH_MONITOR") not in {"1", "true", "yes"}:
        monitor = get_monitor()
        await monitor.start()
    try:
        yield
    finally:
        if monitor is not None:
            await monitor.stop()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    mimetypes.add_type("image/svg+xml", ".svg")

    app = FastAPI(
        title="AutoApply",
        description="AI-powered job application automation web API",
        version="0.7.0",
        lifespan=_lifespan,
    )

    app.mount(
        "/assets",
        StaticFiles(directory=str(FRONTEND_ASSETS_DIR), check_dir=False),
        name="frontend_assets",
    )

    from src.web.routes.agent import router as agent_router
    from src.web.routes.api import router as api_router
    from src.web.routes.review import router as review_router  # Phase 17.3
    from src.web.routes.tasks import router as tasks_router  # Phase 14.8

    app.include_router(api_router)
    app.include_router(agent_router)
    app.include_router(tasks_router)
    app.include_router(review_router)

    @app.get("/", include_in_schema=False)
    async def spa_root():
        return _frontend_html()

    @app.get("/favicon.svg", include_in_schema=False)
    async def favicon_svg():
        if FRONTEND_FAVICON.exists():
            return FileResponse(FRONTEND_FAVICON, media_type="image/svg+xml")
        return HTMLResponse("Not Found", status_code=404)

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon_ico():
        return RedirectResponse("/favicon.svg")

    @app.get("/logo.svg", include_in_schema=False)
    async def logo_svg():
        if FRONTEND_LOGO.exists():
            return FileResponse(FRONTEND_LOGO, media_type="image/svg+xml")
        return HTMLResponse("Not Found", status_code=404)

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_routes(full_path: str):
        # Reserve /api/* for the JSON API and /assets/* for the bundled SPA
        # asset chunks. Everything else falls back to index.html so client-
        # side router paths (e.g. /materials, /materials/templates,
        # /profile/<id>) survive a hard refresh; vue-router handles "not
        # found" rendering itself.
        if full_path.startswith("api") or full_path.startswith("assets"):
            return HTMLResponse("Not Found", status_code=404)

        return _frontend_html()

    return app
