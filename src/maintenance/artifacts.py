"""Phase 18.4: reference-aware artifact cleanup with quarantine.

``data/output/`` accumulates throughout normal use: every successful
materials generation drops resume+cover-letter files, every failed
``patch_existing`` run leaves a half-written ``patched_resume_<uuid>.docx``,
every form-fill attempt writes a screenshot, and every regeneration
keeps the prior artifact path live on the ``Application`` row.

The cleanup contract (see D028) is:

1. **Reference-based protection.** Before deleting anything we build
   a *protected set* of paths the database considers load-bearing:
   live ``Application.resume_version`` / ``cover_letter_version``,
   ``UserDocument.storage_path``, ``SourceResume.storage_path``,
   ``ReviewQueueEntry.materials_path`` and any ``TaskRecord.result``
   artifact path. Anything in the protected set is *never* touched
   by automatic cleanup.
2. **Classification.** Unprotected files are bucketed by
   :func:`classify_path` into ``tmp`` (half-written
   ``*.tmp`` siblings), ``failed_artifact`` (zero-byte / unreadable
   DOCX/PDF), ``screenshot`` (under ``data/output/screenshots``),
   ``version_log`` (under ``data/output/versions``), ``orphan_output``
   (everything else under ``data/output`` directly), or ``unknown``.
3. **Quarantine.** Eligible files are *moved* into
   ``data/quarantine/<run_id>/...`` mirroring their relative path
   under ``data/output``. Permanent deletion only happens after the
   ``cleanup.quarantine_days`` window when :func:`purge_quarantine`
   runs, so a wrongful delete is recoverable for a week.
4. **Audit.** Every run inserts one :class:`CleanupRun` row plus one
   :class:`CleanupItem` per candidate so the operator can answer
   "what did cleanup do last Tuesday?" without grepping logs.

The same code drives both the scheduled
``maintenance.cache_eviction`` Beat task and the manual
``autoapply cleanup`` CLI -- there is intentionally no "manual rules"
fork.
"""

from __future__ import annotations

import logging
import shutil
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.config import PROJECT_ROOT, load_config
from src.core.models import (
    Application,
    CleanupItem,
    CleanupRun,
    GateRequest,
    ReviewQueueEntry,
    SourceResume,
    TaskRecord,
    UserDocument,
)
from src.maintenance.atomic import TMP_SUFFIX

logger = logging.getLogger(__name__)


# Filesystem layout -----------------------------------------------------

#: Where generators write artifacts. Subdirectories ``screenshots`` /
#: ``versions`` are special-cased by the classifier.
OUTPUT_ROOT: Path = PROJECT_ROOT / "data" / "output"

#: Where ``clean()`` moves eligible files. ``purge_quarantine()`` is the
#: only path that permanently deletes from here.
QUARANTINE_ROOT: Path = PROJECT_ROOT / "data" / "quarantine"

#: Subdirectory under ``data/output`` for form-fill / submit screenshots.
SCREENSHOTS_DIRNAME = "screenshots"

#: Subdirectory under ``data/output`` for ``save_generation_version``
#: JSON dumps.
VERSIONS_DIRNAME = "versions"


# Categories returned by :func:`classify_path` ------------------------

CATEGORY_PROTECTED = "protected"
CATEGORY_TMP = "tmp"
CATEGORY_FAILED_ARTIFACT = "failed_artifact"
CATEGORY_SCREENSHOT = "screenshot"
CATEGORY_VERSION_LOG = "version_log"
CATEGORY_ORPHAN_OUTPUT = "orphan_output"
CATEGORY_UNKNOWN = "unknown"

CATEGORIES = (
    CATEGORY_PROTECTED,
    CATEGORY_TMP,
    CATEGORY_FAILED_ARTIFACT,
    CATEGORY_SCREENSHOT,
    CATEGORY_VERSION_LOG,
    CATEGORY_ORPHAN_OUTPUT,
    CATEGORY_UNKNOWN,
)


# Actions recorded on :class:`CleanupItem` ----------------------------

ACTION_SKIP_PROTECTED = "skip_protected"
ACTION_SKIP_RECENT = "skip_recent"
ACTION_SKIP_UNKNOWN = "skip_unknown"
ACTION_QUARANTINED = "quarantined"
ACTION_PURGED = "purged"
ACTION_RESTORED = "restored"
ACTION_ERROR = "error"
ACTION_PLAN_QUARANTINE = "plan_quarantine"  # scan-only verdict


# Default retention windows. The settings.yaml ``cleanup`` block can
# override these per-deployment. Numbers chosen to match the Phase
# 18.4 plan:
#
# * ``tmp`` siblings: 24h is long enough that a still-running write
#   isn't mistaken for a corpse, short enough that a real ghost
#   doesn't linger.
# * ``failed_artifact``: 24-72h. We default to 48h.
# * ``orphan_output``: 30 days; matches the "month of history is
#   probably enough" intuition.
# * ``screenshot``: keep the latest 5 per application; older ones go.
# * ``soft_deleted``: 14 days from ``Application.deleted_at``.
# * ``quarantine_days``: how long to wait before purging from
#   ``data/quarantine``. 7 days.

DEFAULT_RETENTION = {
    "tmp_hours": 24,
    "failed_artifact_hours": 48,
    "orphan_output_days": 30,
    "soft_deleted_retention_days": 14,
    "screenshot_keep_per_application": 5,
    "quarantine_days": 7,
}


@dataclass
class CleanupConfig:
    """Resolved cleanup knobs. ``load_cleanup_config`` reads
    ``settings.yaml`` and merges with the defaults above."""

    tmp_hours: int = DEFAULT_RETENTION["tmp_hours"]
    failed_artifact_hours: int = DEFAULT_RETENTION["failed_artifact_hours"]
    orphan_output_days: int = DEFAULT_RETENTION["orphan_output_days"]
    soft_deleted_retention_days: int = DEFAULT_RETENTION["soft_deleted_retention_days"]
    screenshot_keep_per_application: int = DEFAULT_RETENTION[
        "screenshot_keep_per_application"
    ]
    quarantine_days: int = DEFAULT_RETENTION["quarantine_days"]


def load_cleanup_config(config: dict[str, Any] | None = None) -> CleanupConfig:
    """Read ``cleanup.*`` from settings (with sane defaults).

    Missing block / missing keys / non-int values fall back to the
    defaults so a partial settings.yaml still gives a usable config.
    """
    raw = (config or load_config()).get("cleanup") or {}
    if not isinstance(raw, dict):
        raw = {}

    def _int(key: str, default: int) -> int:
        value = raw.get(key, default)
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        if parsed < 0:
            return default
        return parsed

    return CleanupConfig(
        tmp_hours=_int("tmp_hours", DEFAULT_RETENTION["tmp_hours"]),
        failed_artifact_hours=_int(
            "failed_artifact_hours", DEFAULT_RETENTION["failed_artifact_hours"]
        ),
        orphan_output_days=_int(
            "output_retention_days", DEFAULT_RETENTION["orphan_output_days"]
        ),
        soft_deleted_retention_days=_int(
            "soft_deleted_retention_days",
            DEFAULT_RETENTION["soft_deleted_retention_days"],
        ),
        screenshot_keep_per_application=_int(
            "screenshot_keep_per_application",
            DEFAULT_RETENTION["screenshot_keep_per_application"],
        ),
        quarantine_days=_int(
            "quarantine_days", DEFAULT_RETENTION["quarantine_days"]
        ),
    )


# --- Protected-path collection ---------------------------------------


def build_protected_paths(
    session: Session,
    *,
    tenant_id: str | None = None,
    soft_delete_cutoff: datetime | None = None,
) -> set[Path]:
    """Resolve every path the database considers load-bearing.

    Returned paths are absolute and ``.resolve()``-d so callers can
    membership-test against absolute paths walked off disk. The set
    is intentionally inclusive: when in doubt we keep, because a
    false positive (skipped cleanup) is cheap and a false negative
    (deleting a real asset) is destructive.

    ``soft_delete_cutoff`` is a timestamp; ``Application`` rows whose
    ``deleted_at`` is older than the cutoff are NOT protected so
    cleanup can harvest their artifacts. ``None`` keeps every
    soft-deleted row protected (used by the scan path so the operator
    can preview without the cutoff math).
    """
    protected: set[Path] = set()

    def _add(raw: str | None) -> None:
        if not raw:
            return
        try:
            resolved = _resolve_path(raw)
        except Exception:  # noqa: BLE001 -- malformed paths are ignored
            return
        if resolved is not None:
            protected.add(resolved)

    for raw_resume, raw_cover, files, deleted_at in _collect_application_paths(
        session, tenant_id=tenant_id
    ):
        if (
            deleted_at is not None
            and soft_delete_cutoff is not None
            and deleted_at <= soft_delete_cutoff
        ):
            # Past the retention window -- artifacts are eligible for
            # cleanup, so we deliberately omit them from the protected
            # set.
            continue
        _add(raw_resume)
        _add(raw_cover)
        if isinstance(files, list):
            for entry in files:
                _add(entry if isinstance(entry, str) else None)

    # UserDocument library + SourceResume originals.
    for storage in _collect_storage_paths(
        session, UserDocument.storage_path, tenant_id=tenant_id
    ):
        _add(storage)
    for storage in _collect_storage_paths(
        session, SourceResume.storage_path, tenant_id=tenant_id
    ):
        _add(storage)

    # Review queue materials artifacts.
    for materials_path in _collect_storage_paths(
        session, ReviewQueueEntry.materials_path, tenant_id=tenant_id
    ):
        _add(materials_path)

    # Task / gate result payloads can reference artifact paths in the
    # ``result`` / ``payload`` JSONB columns. We walk them defensively.
    for row in session.execute(select(TaskRecord.payload)).scalars():
        for value in _walk_string_values(row):
            _add(value)
    for row in session.execute(select(GateRequest.payload)).scalars():
        for value in _walk_string_values(row):
            _add(value)

    return protected


def _collect_application_paths(
    session: Session, *, tenant_id: str | None
) -> Iterable[tuple[str | None, str | None, list[Any] | None, datetime | None]]:
    stmt = select(
        Application.resume_version,
        Application.cover_letter_version,
        Application.files_uploaded,
        Application.deleted_at,
    )
    if tenant_id is not None:
        stmt = stmt.where(Application.tenant_id == tenant_id)
    for resume, cover, files, deleted_at in session.execute(stmt).all():
        yield resume, cover, files, deleted_at


def _collect_storage_paths(
    session: Session, column: Any, *, tenant_id: str | None
):
    stmt = select(column)
    if tenant_id is not None and hasattr(column.parent, "entity"):
        # ``column.parent.entity`` is the mapped class; reach for its
        # ``tenant_id`` attribute when present.
        cls = column.parent.entity
        if hasattr(cls, "tenant_id"):
            stmt = stmt.where(cls.tenant_id == tenant_id)
    for value in session.execute(stmt).scalars():
        if isinstance(value, str):
            yield value


def _walk_string_values(blob: Any) -> Iterable[str]:
    """Walk a JSONB-shaped payload and yield every string leaf.

    Defensive: payloads we don't fully control may contain arbitrary
    string keys + values; we yield everything because the calling
    ``_resolve_path`` filters by "does this look like a path under
    ``data/``" rather than trusting the caller's structure.
    """
    if blob is None:
        return
    if isinstance(blob, str):
        yield blob
        return
    if isinstance(blob, dict):
        for value in blob.values():
            yield from _walk_string_values(value)
        return
    if isinstance(blob, list | tuple):
        for value in blob:
            yield from _walk_string_values(value)
        return


def _resolve_path(raw: str) -> Path | None:
    """Coerce a possibly-relative path into an absolute resolved Path.

    Returns ``None`` for strings that obviously aren't filesystem
    paths (URLs, integers, empty strings) so the protected-set walk
    over arbitrary JSONB doesn't drag in false positives.
    """
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    if text.startswith(("http://", "https://", "redis://", "postgres", "smtp")):
        return None
    candidate = Path(text)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    try:
        return candidate.resolve()
    except (OSError, RuntimeError):
        return candidate


# --- Classification --------------------------------------------------


@dataclass
class Classification:
    """Result of :func:`classify_path` for a single file. ``eligible``
    means automatic cleanup is allowed to quarantine it (subject to
    age cutoffs)."""

    category: str
    eligible: bool
    reason: str


def classify_path(
    path: Path,
    *,
    protected: set[Path],
) -> Classification:
    """Bucket a single file under ``data/output`` by category.

    Protected paths short-circuit to ``protected`` regardless of name;
    everything else is decided by suffix + parent directory. The
    function is intentionally pure -- no FS reads beyond ``.stat()``
    -- so the rules can be unit-tested deterministically.
    """
    try:
        abs_path = path.resolve()
    except (OSError, RuntimeError):
        abs_path = path

    if abs_path in protected:
        return Classification(
            category=CATEGORY_PROTECTED,
            eligible=False,
            reason="referenced by DB",
        )

    name = abs_path.name
    suffix = abs_path.suffix.lower()

    # ``.tmp`` siblings dropped by atomic_write that never got renamed.
    # We also catch legacy "*.part" half-writes from older builds.
    if suffix == TMP_SUFFIX or name.endswith(".part"):
        return Classification(
            category=CATEGORY_TMP,
            eligible=True,
            reason="atomic_write tmp leftover",
        )

    parts = {p.name for p in abs_path.parents}
    if SCREENSHOTS_DIRNAME in parts:
        return Classification(
            category=CATEGORY_SCREENSHOT,
            eligible=True,
            reason="form-fill screenshot",
        )
    if VERSIONS_DIRNAME in parts:
        return Classification(
            category=CATEGORY_VERSION_LOG,
            eligible=True,
            reason="generation version log",
        )

    # Failed artifact: zero-byte DOCX/PDF on disk are almost certainly
    # crashed patch attempts. Size is the cheap signal; deep validation
    # (does python-docx open it?) is intentionally out of scope -- it
    # would gate cleanup on installable libraries.
    if suffix in (".docx", ".pdf"):
        try:
            size = abs_path.stat().st_size
        except OSError:
            size = -1
        if size == 0:
            return Classification(
                category=CATEGORY_FAILED_ARTIFACT,
                eligible=True,
                reason="zero-byte artifact",
            )
        if size < 0:
            return Classification(
                category=CATEGORY_UNKNOWN,
                eligible=False,
                reason="stat failed",
            )

    # Anything else under ``data/output`` that isn't protected and
    # doesn't live in a special subdirectory is an orphan output. The
    # age cutoff (``orphan_output_days``) is applied by the caller so
    # the unit tests for this function don't have to fake time.
    return Classification(
        category=CATEGORY_ORPHAN_OUTPUT,
        eligible=True,
        reason="not referenced by DB",
    )


# --- Scan / clean / restore / purge ----------------------------------


@dataclass
class CleanupCandidate:
    """In-memory record produced by :func:`scan` before any FS mutation."""

    path: Path
    category: str
    eligible: bool
    reason: str
    size_bytes: int | None
    mtime: datetime | None
    age_seconds: float | None
    action: str  # one of the ACTION_* constants


@dataclass
class CleanupReport:
    """Returned by :func:`scan` / :func:`clean` / :func:`purge_quarantine`.

    JSON-serialisable scalar fields only so the result can be
    persisted into ``CleanupRun.summary`` verbatim. ``items`` is a
    list of plain dicts (one per :class:`CleanupCandidate`).
    """

    run_id: str
    tenant_id: str
    mode: str
    trigger: str
    started_at: datetime
    finished_at: datetime | None = None
    scanned_count: int = 0
    protected_count: int = 0
    quarantined_count: int = 0
    purged_count: int = 0
    restored_count: int = 0
    error_count: int = 0
    bytes_reclaimed: int = 0
    items: list[dict[str, Any]] = field(default_factory=list)

    def to_summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "mode": self.mode,
            "trigger": self.trigger,
            "tenant_id": self.tenant_id,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat()
            if self.finished_at
            else None,
            "scanned_count": self.scanned_count,
            "protected_count": self.protected_count,
            "quarantined_count": self.quarantined_count,
            "purged_count": self.purged_count,
            "restored_count": self.restored_count,
            "error_count": self.error_count,
            "bytes_reclaimed": self.bytes_reclaimed,
        }


def scan(
    session: Session,
    *,
    tenant_id: str,
    output_root: Path | None = None,
    now: datetime | None = None,
    config: CleanupConfig | None = None,
    trigger: str = "manual",
) -> CleanupReport:
    """Walk ``data/output`` and produce a per-file verdict without
    touching disk. Each candidate is recorded with ``action`` set to
    :data:`ACTION_PLAN_QUARANTINE` (cleanup would move it), one of
    the ``skip_*`` actions (cleanup wouldn't), or :data:`ACTION_ERROR`.

    Behaviour matches :func:`clean` except no files are moved and no
    DB rows are written.
    """
    root = output_root or OUTPUT_ROOT
    now = now or datetime.now(UTC)
    cfg = config or load_cleanup_config()
    protected = build_protected_paths(
        session,
        tenant_id=tenant_id,
        soft_delete_cutoff=now - timedelta(days=cfg.soft_deleted_retention_days),
    )

    candidates = _collect_candidates(
        root=root, protected=protected, now=now, cfg=cfg
    )

    report = CleanupReport(
        run_id=uuid.uuid4().hex,
        tenant_id=tenant_id,
        mode="scan",
        trigger=trigger,
        started_at=now,
    )
    for cand in candidates:
        report.scanned_count += 1
        if cand.category == CATEGORY_PROTECTED:
            report.protected_count += 1
        report.items.append(_candidate_to_dict(cand))
    report.finished_at = datetime.now(UTC)
    return report


def clean(
    session: Session,
    *,
    tenant_id: str,
    output_root: Path | None = None,
    quarantine_root: Path | None = None,
    now: datetime | None = None,
    config: CleanupConfig | None = None,
    trigger: str = "manual",
) -> CleanupReport:
    """Walk ``data/output``, move eligible files to
    ``data/quarantine/<run_id>/``, and write a :class:`CleanupRun`
    row + one :class:`CleanupItem` per candidate.

    Caller commits. (The intent is to keep the move + audit atomic at
    the SQL boundary; the file moves are not transactional, but the
    audit row is the durable record so a half-completed run is still
    recoverable.)
    """
    root = output_root or OUTPUT_ROOT
    quarantine = quarantine_root or QUARANTINE_ROOT
    now = now or datetime.now(UTC)
    cfg = config or load_cleanup_config()
    protected = build_protected_paths(
        session,
        tenant_id=tenant_id,
        soft_delete_cutoff=now - timedelta(days=cfg.soft_deleted_retention_days),
    )

    run_id = uuid.uuid4()
    run_quarantine_dir = quarantine / run_id.hex
    candidates = _collect_candidates(
        root=root, protected=protected, now=now, cfg=cfg
    )

    run = CleanupRun(
        id=run_id,
        tenant_id=tenant_id,
        mode="clean",
        trigger=trigger,
        started_at=now,
    )
    session.add(run)
    session.flush()

    report = CleanupReport(
        run_id=run_id.hex,
        tenant_id=tenant_id,
        mode="clean",
        trigger=trigger,
        started_at=now,
    )

    for cand in candidates:
        report.scanned_count += 1

        if cand.category == CATEGORY_PROTECTED:
            report.protected_count += 1
            _write_item(
                session=session,
                run_id=run_id,
                tenant_id=tenant_id,
                cand=cand,
                action=ACTION_SKIP_PROTECTED,
                now=now,
            )
            report.items.append(_candidate_to_dict(cand, override_action=ACTION_SKIP_PROTECTED))
            continue

        if cand.action == ACTION_SKIP_RECENT or not cand.eligible:
            _write_item(
                session=session,
                run_id=run_id,
                tenant_id=tenant_id,
                cand=cand,
                action=cand.action if cand.action.startswith("skip") else ACTION_SKIP_UNKNOWN,
                now=now,
            )
            report.items.append(_candidate_to_dict(cand))
            continue

        try:
            relative = cand.path.resolve().relative_to(root.resolve())
        except (ValueError, OSError):
            relative = Path(cand.path.name)
        destination = run_quarantine_dir / relative

        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(cand.path), str(destination))
        except OSError as exc:
            logger.warning("cleanup: could not move %s -> %s: %s", cand.path, destination, exc)
            report.error_count += 1
            _write_item(
                session=session,
                run_id=run_id,
                tenant_id=tenant_id,
                cand=cand,
                action=ACTION_ERROR,
                now=now,
                reason=f"move failed: {exc}",
            )
            report.items.append(
                _candidate_to_dict(cand, override_action=ACTION_ERROR)
            )
            continue

        report.quarantined_count += 1
        if cand.size_bytes:
            report.bytes_reclaimed += int(cand.size_bytes)
        _write_item(
            session=session,
            run_id=run_id,
            tenant_id=tenant_id,
            cand=cand,
            action=ACTION_QUARANTINED,
            now=now,
            quarantine_path=str(destination),
        )
        report.items.append(
            _candidate_to_dict(
                cand,
                override_action=ACTION_QUARANTINED,
                quarantine_path=str(destination),
            )
        )

    report.finished_at = datetime.now(UTC)
    _finalise_run(run, report)
    return report


def purge_quarantine(
    session: Session,
    *,
    tenant_id: str,
    quarantine_root: Path | None = None,
    now: datetime | None = None,
    config: CleanupConfig | None = None,
    trigger: str = "manual",
) -> CleanupReport:
    """Permanently delete quarantine entries older than
    ``cleanup.quarantine_days``.

    The grace period is anchored on ``CleanupRun.started_at`` rather
    than file mtime so the operator can predict when a given
    ``data/quarantine/<run_id>`` directory will disappear regardless
    of FS clock skew.
    """
    quarantine = quarantine_root or QUARANTINE_ROOT
    now = now or datetime.now(UTC)
    cfg = config or load_cleanup_config()

    purge_run_id = uuid.uuid4()
    cutoff = now - timedelta(days=cfg.quarantine_days)
    eligible_runs = (
        session.execute(
            select(CleanupRun)
            .where(CleanupRun.tenant_id == tenant_id)
            .where(CleanupRun.mode == "clean")
            .where(CleanupRun.started_at <= cutoff)
        )
        .scalars()
        .all()
    )

    purge_run = CleanupRun(
        id=purge_run_id,
        tenant_id=tenant_id,
        mode="purge_quarantine",
        trigger=trigger,
        started_at=now,
    )
    session.add(purge_run)
    session.flush()

    report = CleanupReport(
        run_id=purge_run_id.hex,
        tenant_id=tenant_id,
        mode="purge_quarantine",
        trigger=trigger,
        started_at=now,
    )

    for run in eligible_runs:
        run_dir = quarantine / run.id.hex
        if not run_dir.exists():
            continue
        for entry in _iter_files(run_dir):
            try:
                size = entry.stat().st_size
            except OSError:
                size = 0
            try:
                entry.unlink()
            except OSError as exc:
                logger.warning("purge_quarantine: could not unlink %s: %s", entry, exc)
                report.error_count += 1
                continue
            report.purged_count += 1
            report.bytes_reclaimed += size

        # Best-effort prune of now-empty intermediate directories.
        for dirpath in sorted(
            (p for p in run_dir.rglob("*") if p.is_dir()), reverse=True
        ):
            try:
                dirpath.rmdir()
            except OSError:
                continue
        try:
            run_dir.rmdir()
        except OSError:
            pass

    report.finished_at = datetime.now(UTC)
    _finalise_run(purge_run, report)
    return report


def restore(
    session: Session,
    *,
    tenant_id: str,
    run_id: str,
    path: str,
    output_root: Path | None = None,
    quarantine_root: Path | None = None,
    now: datetime | None = None,
    trigger: str = "manual",
) -> CleanupReport:
    """Move a quarantined item back to its original location.

    Looks up the :class:`CleanupItem` row by ``(run_id, path)`` so the
    caller cannot accidentally restore something that wasn't
    quarantined by AutoApply. Returns a single-item report.
    """
    output = output_root or OUTPUT_ROOT
    quarantine = quarantine_root or QUARANTINE_ROOT
    now = now or datetime.now(UTC)

    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError as exc:
        raise ValueError(f"invalid run_id: {run_id!r}") from exc

    item: CleanupItem | None = session.execute(
        select(CleanupItem)
        .where(CleanupItem.run_id == run_uuid)
        .where(CleanupItem.tenant_id == tenant_id)
        .where(CleanupItem.path == path)
        .where(CleanupItem.action == ACTION_QUARANTINED)
    ).scalar_one_or_none()

    restore_run_id = uuid.uuid4()
    restore_run = CleanupRun(
        id=restore_run_id,
        tenant_id=tenant_id,
        mode="restore",
        trigger=trigger,
        started_at=now,
    )
    session.add(restore_run)
    session.flush()

    report = CleanupReport(
        run_id=restore_run_id.hex,
        tenant_id=tenant_id,
        mode="restore",
        trigger=trigger,
        started_at=now,
    )

    if item is None or not item.quarantine_path:
        report.error_count += 1
        report.finished_at = datetime.now(UTC)
        _finalise_run(restore_run, report)
        return report

    source = Path(item.quarantine_path)
    target = Path(item.path)
    if not target.is_absolute():
        target = (output / target).resolve()

    if not source.exists():
        report.error_count += 1
    else:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
            report.restored_count += 1
            item.action = ACTION_RESTORED
            item.quarantine_path = None
        except OSError as exc:
            logger.warning("restore: %s -> %s failed: %s", source, target, exc)
            report.error_count += 1

    _ = quarantine  # quarantine kwarg accepted for symmetry / future use
    report.finished_at = datetime.now(UTC)
    _finalise_run(restore_run, report)
    return report


# --- Internal helpers ------------------------------------------------


def _collect_candidates(
    *,
    root: Path,
    protected: set[Path],
    now: datetime,
    cfg: CleanupConfig,
) -> list[CleanupCandidate]:
    """Walk ``root`` once, classify each file, apply age cutoffs."""
    if not root.exists():
        return []

    candidates: list[CleanupCandidate] = []
    screenshot_files_by_prefix: dict[str, list[tuple[float, Path]]] = {}

    for path in _iter_files(root):
        try:
            stat = path.stat()
        except OSError:
            continue
        size = stat.st_size
        mtime_dt = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
        age_seconds = (now - mtime_dt).total_seconds()

        classification = classify_path(path, protected=protected)

        action = ACTION_PLAN_QUARANTINE
        eligible = classification.eligible

        if classification.category == CATEGORY_PROTECTED:
            action = ACTION_SKIP_PROTECTED
        elif classification.category == CATEGORY_TMP:
            if age_seconds < cfg.tmp_hours * 3600:
                action = ACTION_SKIP_RECENT
                eligible = False
        elif classification.category == CATEGORY_FAILED_ARTIFACT:
            if age_seconds < cfg.failed_artifact_hours * 3600:
                action = ACTION_SKIP_RECENT
                eligible = False
        elif classification.category == CATEGORY_ORPHAN_OUTPUT:
            if age_seconds < cfg.orphan_output_days * 86400:
                action = ACTION_SKIP_RECENT
                eligible = False
        elif classification.category == CATEGORY_SCREENSHOT:
            # Defer the "keep the latest N per application" decision
            # until we've seen every screenshot for the prefix.
            prefix = path.name.split("_", 1)[0] if "_" in path.name else path.stem
            screenshot_files_by_prefix.setdefault(prefix, []).append(
                (stat.st_mtime, path)
            )
            # Don't append yet; we re-emit screenshots below.
            continue
        elif classification.category == CATEGORY_VERSION_LOG:
            if age_seconds < cfg.orphan_output_days * 86400:
                action = ACTION_SKIP_RECENT
                eligible = False
        elif classification.category == CATEGORY_UNKNOWN:
            action = ACTION_SKIP_UNKNOWN
            eligible = False

        candidates.append(
            CleanupCandidate(
                path=path,
                category=classification.category,
                eligible=eligible,
                reason=classification.reason,
                size_bytes=size,
                mtime=mtime_dt,
                age_seconds=age_seconds,
                action=action,
            )
        )

    # Screenshot decisions: keep latest N per prefix, the rest become
    # eligible regardless of age.
    keep = max(0, cfg.screenshot_keep_per_application)
    for prefix, entries in screenshot_files_by_prefix.items():
        entries.sort(key=lambda pair: pair[0], reverse=True)
        for idx, (mtime, path) in enumerate(entries):
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            mtime_dt = datetime.fromtimestamp(mtime, tz=UTC)
            age_seconds = (now - mtime_dt).total_seconds()
            if idx < keep:
                candidates.append(
                    CleanupCandidate(
                        path=path,
                        category=CATEGORY_SCREENSHOT,
                        eligible=False,
                        reason=f"latest-{keep} for {prefix}",
                        size_bytes=size,
                        mtime=mtime_dt,
                        age_seconds=age_seconds,
                        action=ACTION_SKIP_RECENT,
                    )
                )
            else:
                candidates.append(
                    CleanupCandidate(
                        path=path,
                        category=CATEGORY_SCREENSHOT,
                        eligible=True,
                        reason=f"older than latest-{keep}",
                        size_bytes=size,
                        mtime=mtime_dt,
                        age_seconds=age_seconds,
                        action=ACTION_PLAN_QUARANTINE,
                    )
                )

    return candidates


def _iter_files(root: Path) -> Iterable[Path]:
    """Yield every regular file under ``root`` (recursively).

    Skips symlinks because following them out of ``data/output`` would
    let a malicious / misplaced symlink trick cleanup into walking
    elsewhere on disk.
    """
    if not root.exists():
        return
    for path in root.rglob("*"):
        try:
            if path.is_symlink() or not path.is_file():
                continue
        except OSError:
            continue
        yield path


def _candidate_to_dict(
    cand: CleanupCandidate,
    *,
    override_action: str | None = None,
    quarantine_path: str | None = None,
) -> dict[str, Any]:
    return {
        "path": str(cand.path),
        "category": cand.category,
        "action": override_action or cand.action,
        "eligible": cand.eligible,
        "reason": cand.reason,
        "size_bytes": cand.size_bytes,
        "mtime": cand.mtime.isoformat() if cand.mtime else None,
        "age_seconds": cand.age_seconds,
        "quarantine_path": quarantine_path,
    }


def _write_item(
    *,
    session: Session,
    run_id: uuid.UUID,
    tenant_id: str,
    cand: CleanupCandidate,
    action: str,
    now: datetime,
    reason: str | None = None,
    quarantine_path: str | None = None,
) -> CleanupItem:
    item = CleanupItem(
        run_id=run_id,
        tenant_id=tenant_id,
        path=str(cand.path),
        quarantine_path=quarantine_path,
        category=cand.category,
        action=action,
        size_bytes=cand.size_bytes,
        mtime=cand.mtime,
        quarantined_at=now if action == ACTION_QUARANTINED else None,
        reason=reason or cand.reason,
    )
    session.add(item)
    return item


def _finalise_run(run: CleanupRun, report: CleanupReport) -> None:
    run.finished_at = report.finished_at
    run.scanned_count = report.scanned_count
    run.protected_count = report.protected_count
    run.quarantined_count = report.quarantined_count
    run.purged_count = report.purged_count
    run.restored_count = report.restored_count
    run.error_count = report.error_count
    run.bytes_reclaimed = report.bytes_reclaimed
    run.summary = report.to_summary()


__all__ = [
    "ACTION_ERROR",
    "ACTION_PLAN_QUARANTINE",
    "ACTION_PURGED",
    "ACTION_QUARANTINED",
    "ACTION_RESTORED",
    "ACTION_SKIP_PROTECTED",
    "ACTION_SKIP_RECENT",
    "ACTION_SKIP_UNKNOWN",
    "CATEGORIES",
    "CATEGORY_FAILED_ARTIFACT",
    "CATEGORY_ORPHAN_OUTPUT",
    "CATEGORY_PROTECTED",
    "CATEGORY_SCREENSHOT",
    "CATEGORY_TMP",
    "CATEGORY_UNKNOWN",
    "CATEGORY_VERSION_LOG",
    "Classification",
    "CleanupCandidate",
    "CleanupConfig",
    "CleanupReport",
    "DEFAULT_RETENTION",
    "OUTPUT_ROOT",
    "QUARANTINE_ROOT",
    "build_protected_paths",
    "classify_path",
    "clean",
    "load_cleanup_config",
    "purge_quarantine",
    "restore",
    "scan",
]
