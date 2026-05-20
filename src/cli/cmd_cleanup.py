"""Phase 18.4: ``autoapply cleanup`` command group.

Four sub-commands share the same code path that the Beat-driven
``maintenance.cache_eviction`` task uses (see
:mod:`src.maintenance.artifacts`). Manual and scheduled cleanup
intentionally cannot diverge -- the rules are computed in one place
so a user can rehearse a run with ``scan`` and trust the next
scheduled tick will do the same thing.
"""

from __future__ import annotations

import json

import click


@click.group("cleanup")
def cleanup_cmd() -> None:
    """Inspect and operate the artifact cleanup pipeline."""


@cleanup_cmd.command("scan")
@click.option(
    "--tenant", default="default", show_default=True, help="Tenant id to scope to."
)
@click.option(
    "--json", "as_json", is_flag=True, help="Emit the per-file plan as JSON."
)
def scan_cmd(tenant: str, as_json: bool) -> None:
    """Dry-run: classify every file under ``data/output`` and report
    what cleanup would do. Touches no files, writes no DB rows."""
    from src.core.database import get_session_factory
    from src.maintenance.artifacts import scan

    factory = get_session_factory()
    with factory() as session:
        report = scan(session, tenant_id=tenant, trigger="manual")
    _emit(report, as_json=as_json)


@cleanup_cmd.command("clean")
@click.option(
    "--tenant", default="default", show_default=True, help="Tenant id to scope to."
)
@click.option(
    "--json", "as_json", is_flag=True, help="Emit the per-file plan as JSON."
)
def clean_cmd(tenant: str, as_json: bool) -> None:
    """Run cleanup: move eligible files to ``data/quarantine/<run_id>``
    and write CleanupRun + CleanupItem audit rows."""
    from src.core.database import get_session_factory
    from src.maintenance.artifacts import clean

    factory = get_session_factory()
    with factory() as session, session.begin():
        report = clean(session, tenant_id=tenant, trigger="manual")
    _emit(report, as_json=as_json)


@cleanup_cmd.command("restore")
@click.argument("run_id")
@click.argument("path")
@click.option(
    "--tenant", default="default", show_default=True, help="Tenant id to scope to."
)
def restore_cmd(run_id: str, path: str, tenant: str) -> None:
    """Move a quarantined file back to its original location.

    ``RUN_ID`` is the cleanup run's UUID (printed by ``scan`` / ``clean``);
    ``PATH`` is the original absolute path the item was harvested from.
    """
    from src.core.database import get_session_factory
    from src.maintenance.artifacts import restore

    factory = get_session_factory()
    with factory() as session, session.begin():
        report = restore(
            session,
            tenant_id=tenant,
            run_id=run_id,
            path=path,
            trigger="manual",
        )
    _emit(report, as_json=False)


@cleanup_cmd.command("purge-quarantine")
@click.option(
    "--tenant", default="default", show_default=True, help="Tenant id to scope to."
)
def purge_cmd(tenant: str) -> None:
    """Permanently delete quarantine entries older than the
    ``cleanup.quarantine_days`` window."""
    from src.core.database import get_session_factory
    from src.maintenance.artifacts import purge_quarantine

    factory = get_session_factory()
    with factory() as session, session.begin():
        report = purge_quarantine(
            session, tenant_id=tenant, trigger="manual"
        )
    _emit(report, as_json=False)


def _emit(report, *, as_json: bool) -> None:
    summary = report.to_summary()
    if as_json:
        click.echo(
            json.dumps(
                {"summary": summary, "items": report.items},
                indent=2,
                default=str,
            )
        )
        return

    click.echo(f"cleanup run {summary['run_id']} ({summary['mode']})")
    click.echo(f"  tenant:      {summary['tenant_id']}")
    click.echo(f"  scanned:     {summary['scanned_count']}")
    click.echo(f"  protected:   {summary['protected_count']}")
    click.echo(f"  quarantined: {summary['quarantined_count']}")
    click.echo(f"  purged:      {summary['purged_count']}")
    click.echo(f"  restored:    {summary['restored_count']}")
    click.echo(f"  errors:      {summary['error_count']}")
    click.echo(f"  reclaimed:   {summary['bytes_reclaimed']} bytes")


__all__ = ["cleanup_cmd"]
