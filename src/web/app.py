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


def _materials_review_html() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Materials Review — AutoApply</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, sans-serif; background: #0f1117; color: #e2e8f0; padding: 24px; }
  h1 { font-size: 1.4rem; font-weight: 600; margin-bottom: 4px; }
  .subtitle { color: #94a3b8; font-size: 0.85rem; margin-bottom: 24px; }
  .filters { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
  .filter-btn { padding: 5px 14px; border-radius: 20px; border: 1px solid #334155;
    background: #1e293b; color: #94a3b8; cursor: pointer; font-size: 0.8rem; }
  .filter-btn.active { background: #3b82f6; border-color: #3b82f6; color: #fff; }
  table { width: 100%; border-collapse: collapse; font-size: 0.875rem; }
  th { text-align: left; padding: 10px 12px; border-bottom: 1px solid #1e293b;
    color: #64748b; font-weight: 500; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; }
  td { padding: 12px; border-bottom: 1px solid #1e293b; vertical-align: middle; }
  tr:hover td { background: #1e293b44; }
  .company { font-weight: 600; }
  .title { color: #94a3b8; font-size: 0.82rem; }
  .status { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 0.75rem; font-weight: 500; }
  .status-pending { background: #1e3a5f; color: #60a5fa; }
  .status-approved { background: #14532d; color: #4ade80; }
  .status-rejected { background: #450a0a; color: #f87171; }
  .status-submitted { background: #312e81; color: #a78bfa; }
  .btn { display: inline-block; padding: 4px 10px; border-radius: 6px; font-size: 0.78rem;
    text-decoration: none; cursor: pointer; border: none; margin-right: 4px; }
  .btn-resume { background: #1e40af; color: #bfdbfe; }
  .btn-cover { background: #4338ca; color: #c7d2fe; }
  .btn-resume:hover { background: #2563eb; }
  .btn-cover:hover { background: #4f46e5; }
  .btn-approve { background: #166534; color: #86efac; }
  .btn-reject { background: #7f1d1d; color: #fca5a5; }
  .btn-approve:hover { background: #15803d; }
  .btn-reject:hover { background: #991b1b; }
  .no-file { color: #475569; font-size: 0.78rem; font-style: italic; }
  .score { font-size: 0.8rem; color: #94a3b8; }
  #status-bar { color: #64748b; font-size: 0.8rem; margin-bottom: 12px; }
  .loading { text-align: center; padding: 40px; color: #475569; }
</style>
</head>
<body>
<h1>Materials Review</h1>
<p class="subtitle">Generated resumes &amp; cover letters — click to open in a new tab</p>
<div id="status-bar">Loading...</div>
<div class="filters">
  <button class="filter-btn active" onclick="setFilter('all')">All</button>
  <button class="filter-btn" onclick="setFilter('pending')">Pending</button>
  <button class="filter-btn" onclick="setFilter('approved')">Approved</button>
  <button class="filter-btn" onclick="setFilter('rejected')">Rejected</button>
</div>
<div id="root"><div class="loading">Fetching review queue...</div></div>

<script>
let allEntries = [];
let activeFilter = 'all';

function setFilter(f) {
  activeFilter = f;
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.toggle('active', b.textContent.toLowerCase() === f || (f==='all' && b.textContent==='All')));
  render();
}

function fileUrl(fullPath) {
  if (!fullPath) return null;
  const parts = fullPath.replace(/\\\\/g, '/').split('/');
  return '/output/' + encodeURIComponent(parts[parts.length - 1]);
}

function statusBadge(s) {
  return `<span class="status status-${s}">${s}</span>`;
}

function render() {
  const entries = activeFilter === 'all' ? allEntries : allEntries.filter(e => e.status === activeFilter);
  const total = allEntries.length;
  const shown = entries.length;
  document.getElementById('status-bar').textContent = `Showing ${shown} of ${total} entries`;

  if (!entries.length) {
    document.getElementById('root').innerHTML = '<div class="loading">No entries match this filter.</div>';
    return;
  }

  const rows = entries.map(e => {
    const rPath = e.materials?.resume_docx || e.resume_path || null;
    const cPath = e.materials?.cover_letter_docx || e.cover_letter_path || null;
    // Try to derive from materials_path if explicit paths missing
    const mp = e.materials_path || '';
    const rUrl = rPath ? fileUrl(rPath) : (mp ? fileUrl(mp.replace('cover_', 'resume_')) : null);
    const cUrl = cPath ? fileUrl(cPath) : (mp ? fileUrl(mp.replace('resume_', 'cover_')) : null);

    const rBtn = rUrl ? `<a class="btn btn-resume" href="${rUrl}" target="_blank">Resume</a>` : '<span class="no-file">no resume</span>';
    const cBtn = cUrl ? `<a class="btn btn-cover" href="${cUrl}" target="_blank">Cover Letter</a>` : '<span class="no-file">no cover</span>';

    const score = e.score_breakdown?.total != null ? `<span class="score">${(e.score_breakdown.total * 100).toFixed(0)}%</span>` : '';

    return `<tr>
      <td><div class="company">${e.company || '—'}</div><div class="title">${e.title || '—'}</div></td>
      <td>${statusBadge(e.status || 'pending')}</td>
      <td>${score}</td>
      <td>${rBtn} ${cBtn}</td>
      <td>
        <button class="btn btn-approve" onclick="act('${e.id}','approve')">✓ Approve</button>
        <button class="btn btn-reject" onclick="act('${e.id}','reject')">✗ Skip</button>
      </td>
    </tr>`;
  }).join('');

  document.getElementById('root').innerHTML = `
    <table>
      <thead><tr><th>Job</th><th>Status</th><th>Score</th><th>Files</th><th>Action</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

async function act(id, action) {
  try {
    const r = await fetch(`/api/review/${id}/${action}`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: '{}' });
    if (r.ok) { await load(); } else { alert(`Failed: ${r.status}`); }
  } catch(e) { alert('Error: ' + e.message); }
}

async function load() {
  try {
    const r = await fetch('/api/review');
    const data = await r.json();
    allEntries = (data.entries || data || []).sort((a,b) => new Date(b.created_at||0) - new Date(a.created_at||0));
    render();
  } catch(e) {
    document.getElementById('root').innerHTML = `<div class="loading">Error: ${e.message}</div>`;
  }
}

load();
setInterval(load, 15000);
</script>
</body>
</html>"""


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

    OUTPUT_DIR = PROJECT_ROOT / "data" / "output"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/output",
        StaticFiles(directory=str(OUTPUT_DIR)),
        name="output_files",
    )

    from src.web.routes.agent import router as agent_router
    from src.web.routes.api import router as api_router
    from src.web.routes.review import router as review_router  # Phase 17.3
    from src.web.routes.tasks import router as tasks_router  # Phase 14.8

    app.include_router(api_router)
    app.include_router(agent_router)
    app.include_router(tasks_router)
    app.include_router(review_router)

    @app.get("/api/instance", include_in_schema=False)
    async def instance_identity():
        """Identify this server as an AutoApply instance.

        2026-07-16: used by ``autoapply start``'s single-instance guard —
        when the web port is occupied, the CLI probes this endpoint to
        distinguish "another AutoApply stack is already running" (reuse
        it, don't spawn a duplicate worker/Beat/web on a random port)
        from "some other program holds the port" (error out).
        """
        import os as _os  # noqa: PLC0415

        return {"app": "autoapply", "pid": _os.getpid()}

    @app.get("/", include_in_schema=False)
    async def spa_root():
        return _frontend_html()

    @app.get("/review-materials", include_in_schema=False)
    async def materials_review():
        return HTMLResponse(_materials_review_html())

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
