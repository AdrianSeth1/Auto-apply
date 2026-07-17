"""Phase 17.8 / 18.x: per-document-type material generation defaults.

The user sets one default per document type ('resume', 'cover_letter')
in Settings → Default material strategy. Each default says:

* ``strategy``:
    - ``"regenerate"`` — render fresh from a template using the LLM.
    - ``"patch_existing"`` — start from a library DOCX, replace its
      bullets in place. Honors three knobs (see below) so the user
      can tune how aggressively the LLM rewrites their original.
    - ``"use_library"`` — drop in a library document as-is. No LLM
      involvement, no rendering — the file is copied to the output
      directory and returned. This is the "I already wrote the
      perfect resume, just apply with it" path.
* ``default_template_id``: TemplatePackage id (used when strategy is
  ``regenerate`` and the caller didn't override).
* ``default_document_id``: UserDocument id (used when strategy is
  ``patch_existing`` or ``use_library`` and the caller didn't
  override).
* ``patch_aggressiveness``: ``"conservative"`` / ``"balanced"`` /
  ``"aggressive"``. Controls *bullet-text rewrite intensity* only.
  Section reordering and add/remove-bullet are independent toggles
  (see below) per the user's design: a user can be aggressive on
  wording while keeping the original structure intact.
* ``patch_allow_reorder_sections``: when ``False``, the patch step
  keeps the source DOCX's section order even if the IR suggests a
  different one.
* ``patch_allow_add_remove_bullets``: when ``False``, the patch
  step preserves the exact bullet count of the source DOCX —
  surplus bullets from the IR are dropped, deficit slots are left
  blank rather than removed.

The Jobs page "Generate" button and plan-run automation both ask
:func:`resolve_material_choice` for the effective triple before
calling the materials router. Per-call overrides win over defaults;
defaults win over a fallback ``regenerate`` with the system-default
template.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import yaml

from src.core.config import PROJECT_ROOT

logger = logging.getLogger("autoapply.application.material_defaults")

MATERIAL_DEFAULTS_PATH = PROJECT_ROOT / "config" / "material_defaults.yaml"

SUPPORTED_DOCUMENT_TYPES = ("resume", "cover_letter")
SUPPORTED_STRATEGIES = ("regenerate", "patch_existing", "use_library")
SUPPORTED_PATCH_AGGRESSIVENESS = ("conservative", "balanced", "aggressive")

DEFAULT_PATCH_AGGRESSIVENESS = "balanced"
DEFAULT_PATCH_ALLOW_REORDER_SECTIONS = True
DEFAULT_PATCH_ALLOW_ADD_REMOVE_BULLETS = True


def _empty_default() -> dict[str, Any]:
    return {
        "strategy": "regenerate",
        "default_template_id": "",
        "default_document_id": "",
        "patch_aggressiveness": DEFAULT_PATCH_AGGRESSIVENESS,
        "patch_allow_reorder_sections": DEFAULT_PATCH_ALLOW_REORDER_SECTIONS,
        "patch_allow_add_remove_bullets": DEFAULT_PATCH_ALLOW_ADD_REMOVE_BULLETS,
    }


def load_material_defaults() -> dict[str, dict[str, Any]]:
    """Return ``{document_type: {strategy, default_template_id, default_document_id,
    patch_aggressiveness, patch_allow_reorder_sections,
    patch_allow_add_remove_bullets}}``.

    Missing file = empty defaults for both types.
    """
    if not MATERIAL_DEFAULTS_PATH.exists():
        return {t: _empty_default() for t in SUPPORTED_DOCUMENT_TYPES}
    try:
        with open(MATERIAL_DEFAULTS_PATH, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except Exception:  # noqa: BLE001
        logger.exception("material_defaults.yaml is unreadable; using empties")
        return {t: _empty_default() for t in SUPPORTED_DOCUMENT_TYPES}

    if not isinstance(raw, dict):
        raw = {}
    out: dict[str, dict[str, Any]] = {}
    for doc_type in SUPPORTED_DOCUMENT_TYPES:
        entry = raw.get(doc_type)
        if not isinstance(entry, dict):
            entry = {}
        out[doc_type] = _normalize_default(entry)
    return out


def save_material_defaults(payload: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Validate + persist. Returns the normalized payload that ended up on disk."""
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "error": "Material defaults payload must be an object.",
            "error_code": "invalid_payload",
        }

    normalized: dict[str, dict[str, Any]] = {}
    for doc_type in SUPPORTED_DOCUMENT_TYPES:
        entry = payload.get(doc_type) or {}
        if not isinstance(entry, dict):
            return {
                "ok": False,
                "error": f"Default for {doc_type!r} must be an object.",
                "error_code": "invalid_payload",
            }
        normalized[doc_type] = _normalize_default(entry)

    MATERIAL_DEFAULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MATERIAL_DEFAULTS_PATH, "w", encoding="utf-8") as fh:
        yaml.safe_dump(normalized, fh, sort_keys=True, allow_unicode=False)

    return {
        "ok": True,
        "status": "saved",
        "defaults": normalized,
    }


def load_material_defaults_data() -> dict[str, Any]:
    return {
        "ok": True,
        "defaults": load_material_defaults(),
        "config_path": str(MATERIAL_DEFAULTS_PATH),
    }


def resolve_material_choice(
    *,
    document_type: str,
    override_strategy: str | None = None,
    override_template_id: str | None = None,
    override_document_id: str | None = None,
    override_patch_aggressiveness: str | None = None,
    override_patch_allow_reorder_sections: bool | None = None,
    override_patch_allow_add_remove_bullets: bool | None = None,
) -> dict[str, Any]:
    """Compute the effective generation choice for one document.

    Resolution order, per field: override → saved default → built-in
    fallback. Returns a dict with keys ``strategy``, ``template_id``,
    ``document_id``, ``patch_aggressiveness``,
    ``patch_allow_reorder_sections``,
    ``patch_allow_add_remove_bullets``, and ``source`` (audit string).

    Behavior notes:
      * ``patch_existing`` with no resolved ``document_id`` is downgraded
        to ``regenerate`` so we always produce *something*.
      * ``use_library`` with no resolved ``document_id`` is also
        downgraded to ``regenerate`` — there is nothing to drop in.
    """
    if document_type not in SUPPORTED_DOCUMENT_TYPES:
        raise ValueError(f"unsupported document_type {document_type!r}")
    saved = load_material_defaults().get(document_type, _empty_default())

    strategy = (override_strategy or saved.get("strategy") or "regenerate").strip()
    if strategy not in SUPPORTED_STRATEGIES:
        strategy = "regenerate"

    template_id = (override_template_id or saved.get("default_template_id") or "").strip()
    document_id = (override_document_id or saved.get("default_document_id") or "").strip()

    if override_patch_aggressiveness is not None:
        aggressiveness = str(override_patch_aggressiveness).strip()
    else:
        aggressiveness = str(
            saved.get("patch_aggressiveness") or DEFAULT_PATCH_AGGRESSIVENESS
        ).strip()
    if aggressiveness not in SUPPORTED_PATCH_AGGRESSIVENESS:
        aggressiveness = DEFAULT_PATCH_AGGRESSIVENESS

    if override_patch_allow_reorder_sections is not None:
        allow_reorder = bool(override_patch_allow_reorder_sections)
    else:
        allow_reorder = bool(
            saved.get("patch_allow_reorder_sections", DEFAULT_PATCH_ALLOW_REORDER_SECTIONS)
        )

    if override_patch_allow_add_remove_bullets is not None:
        allow_add_remove = bool(override_patch_allow_add_remove_bullets)
    else:
        allow_add_remove = bool(
            saved.get("patch_allow_add_remove_bullets", DEFAULT_PATCH_ALLOW_ADD_REMOVE_BULLETS)
        )

    # Downgrade strategies that need a document_id when none is set.
    downgraded = False
    if strategy in ("patch_existing", "use_library") and not document_id:
        strategy = "regenerate"
        downgraded = True

    source_parts = []
    if override_strategy:
        source_parts.append("override")
    elif saved.get("strategy"):
        source_parts.append("saved-default")
    else:
        source_parts.append("system-default")
    if downgraded:
        source_parts.append("downgraded-no-source-document")

    return {
        "strategy": strategy,
        "template_id": template_id or None,
        "document_id": document_id or None,
        "patch_aggressiveness": aggressiveness,
        "patch_allow_reorder_sections": allow_reorder,
        "patch_allow_add_remove_bullets": allow_add_remove,
        "source": ",".join(source_parts),
    }


def _normalize_default(entry: dict[str, Any]) -> dict[str, Any]:
    strategy = str(entry.get("strategy") or "regenerate").strip()
    if strategy not in SUPPORTED_STRATEGIES:
        strategy = "regenerate"
    template_id = str(entry.get("default_template_id") or "").strip()
    document_id = str(entry.get("default_document_id") or "").strip()
    # Light validation: if document_id is set, ensure it's a UUID-ish
    # string so we don't persist garbage. Permissive: accept any 32+
    # char hex-ish blob to leave room for future id formats.
    if document_id:
        try:
            UUID(document_id)
        except (TypeError, ValueError):
            document_id = ""

    aggressiveness = str(
        entry.get("patch_aggressiveness") or DEFAULT_PATCH_AGGRESSIVENESS
    ).strip()
    if aggressiveness not in SUPPORTED_PATCH_AGGRESSIVENESS:
        aggressiveness = DEFAULT_PATCH_AGGRESSIVENESS

    # ``bool(<missing>)`` is False; fall back to the defaults so a
    # blank entry still represents the documented system behaviour.
    if "patch_allow_reorder_sections" in entry:
        allow_reorder = bool(entry["patch_allow_reorder_sections"])
    else:
        allow_reorder = DEFAULT_PATCH_ALLOW_REORDER_SECTIONS
    if "patch_allow_add_remove_bullets" in entry:
        allow_add_remove = bool(entry["patch_allow_add_remove_bullets"])
    else:
        allow_add_remove = DEFAULT_PATCH_ALLOW_ADD_REMOVE_BULLETS

    return {
        "strategy": strategy,
        "default_template_id": template_id,
        "default_document_id": document_id,
        "patch_aggressiveness": aggressiveness,
        "patch_allow_reorder_sections": allow_reorder,
        "patch_allow_add_remove_bullets": allow_add_remove,
    }


__all__ = [
    "MATERIAL_DEFAULTS_PATH",
    "SUPPORTED_DOCUMENT_TYPES",
    "SUPPORTED_STRATEGIES",
    "SUPPORTED_PATCH_AGGRESSIVENESS",
    "DEFAULT_PATCH_AGGRESSIVENESS",
    "DEFAULT_PATCH_ALLOW_REORDER_SECTIONS",
    "DEFAULT_PATCH_ALLOW_ADD_REMOVE_BULLETS",
    "load_material_defaults",
    "load_material_defaults_data",
    "resolve_material_choice",
    "save_material_defaults",
]
