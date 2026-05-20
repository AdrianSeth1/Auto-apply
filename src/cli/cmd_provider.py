"""``autoapply provider`` -- manage LLM providers (Phase 10.6).

Subcommands:

* ``provider list``        — show every provider, configured or not.
* ``provider test <id>``   — non-mutating connection probe.
* ``provider set-key <id>``— save an API key (API-key providers only).
* ``provider disconnect <id>`` — drop the saved credential.
* ``provider use <id>``    — set as primary in ``config/settings.yaml``.

There is no ``provider login`` subcommand: the CLI subprocess
providers (claude-cli, codex-cli) own their auth via their own
``login`` commands (run ``claude login`` / ``codex login`` directly).
A future native OAuth provider — where AutoApply owns the OpenAI
client and the OAuth tokens itself — would reintroduce a login
subcommand.

Every subcommand supports ``--json`` so the Web UI / external agents
can consume a stable envelope.
"""

from __future__ import annotations

import getpass
import logging
import sys
from typing import Any

import click
import yaml

from src.cli.output import build_json_payload, emit_json
from src.core.config import PROJECT_ROOT
from src.providers import get_registry
from src.providers.base import (
    AuthType,
    LLMProvider,
    ProviderCredentials,
    ProviderTestResult,
    now_iso,
)

logger = logging.getLogger("autoapply.cli.provider")

_SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"


# ---------------------------------------------------------------------------
# Group
# ---------------------------------------------------------------------------


@click.group("provider")
def provider_cmd() -> None:
    """Manage LLM providers (API keys, OAuth, CLI subprocess)."""


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@provider_cmd.command("list")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON envelope.")
def list_cmd(as_json: bool) -> None:
    """List every known provider and its connection status."""
    registry = get_registry()
    rows = registry.public_view()

    if as_json:
        emit_json(
            build_json_payload(
                command="provider.list",
                data={"ok": True, "providers": rows},
            )
        )
        return

    if not rows:
        click.echo("No providers registered.")
        return

    # Compact human view. ASCII-only markers so Windows cp1252
    # consoles render cleanly without UnicodeEncodeError.
    width = max(len(r["id"]) for r in rows)
    for row in rows:
        marker = "[x]" if row["configured"] else "[ ]"
        click.echo(
            f"  {marker} {row['id']:<{width}}  "
            f"[{row['auth_type']:<10}] {row['display_name']}"
        )
        # Phase 17.9.10: surface the currently-configured model + any
        # base_url override so `provider list` shows what gets called,
        # not just whether the slot is filled.
        creds = row.get("credentials") or {}
        metadata = creds.get("metadata") or {}
        model = metadata.get("model")
        base_url = metadata.get("base_url")
        if model or base_url:
            bits = []
            if model:
                bits.append(f"model={model}")
            if base_url:
                bits.append(f"base_url={base_url}")
            click.echo(f"      {' '.join(bits)}")
        if row.get("install_hint") and not row["configured"]:
            click.echo(f"      hint: {row['install_hint']}")


# ---------------------------------------------------------------------------
# test
# ---------------------------------------------------------------------------


@provider_cmd.command("test")
@click.argument("provider_id")
@click.option("--timeout", default=15, show_default=True, type=int)
@click.option("--json", "as_json", is_flag=True)
def test_cmd(provider_id: str, timeout: int, as_json: bool) -> None:
    """Run a non-mutating connection probe."""
    provider = _get_or_die(provider_id, as_json=as_json, command="provider.test")
    try:
        result = provider.test_connection(timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        _emit_error(
            "provider.test",
            f"Connection probe raised: {exc}",
            as_json=as_json,
            exit_code=1,
        )
        return  # pragma: no cover -- _emit_error exits

    if as_json:
        emit_json(
            build_json_payload(
                command="provider.test",
                data={
                    "ok": result.ok,
                    "provider_id": provider_id,
                    "result": result.to_dict(),
                    "error": None if result.ok else result.detail,
                },
            )
        )
    else:
        color = "green" if result.ok else "red"
        click.secho(
            f"{'OK' if result.ok else 'FAIL'}: {result.detail}",
            fg=color,
        )
        if result.latency_ms:
            click.echo(f"  latency: {result.latency_ms} ms")
        if result.model_count is not None:
            click.echo(f"  models:  {result.model_count}")

    if not result.ok:
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# set-key
# ---------------------------------------------------------------------------


@provider_cmd.command("set-key")
@click.argument("provider_id")
@click.option(
    "--api-key",
    default=None,
    help="API key; if omitted, read from stdin without echo.",
)
@click.option(
    "--model",
    default=None,
    help="Default model id (saved into provider metadata).",
)
@click.option(
    "--base-url",
    default=None,
    help="Optional API base URL override.",
)
@click.option("--no-test", is_flag=True, help="Skip the verification round-trip.")
@click.option("--json", "as_json", is_flag=True)
def set_key_cmd(
    provider_id: str,
    api_key: str | None,
    model: str | None,
    base_url: str | None,
    no_test: bool,
    as_json: bool,
) -> None:
    """Save an API key for a provider, then probe the connection."""
    provider = _get_or_die(
        provider_id, as_json=as_json, command="provider.set-key"
    )
    if provider.auth_type is not AuthType.API_KEY:
        _emit_error(
            "provider.set-key",
            f"Provider {provider_id!r} uses auth_type "
            f"{provider.auth_type.value!r}, not 'api_key'. "
            f"Subprocess providers manage their own auth -- "
            f"run `claude login` / `codex login` directly.",
            as_json=as_json,
            exit_code=2,
        )

    # Lazy import so the CLI module doesn't take a hard dep on the
    # API-key subclass at load time.
    from src.providers.api_base import ApiKeyProvider  # noqa: PLC0415

    if not isinstance(provider, ApiKeyProvider):
        _emit_error(
            "provider.set-key",
            f"Provider {provider_id!r} is not an ApiKeyProvider.",
            as_json=as_json,
            exit_code=2,
        )

    allow_empty = getattr(provider, "allow_empty_key", False)
    if api_key is None:
        if sys.stdin.isatty():
            prompt_label = f"Enter API key for {provider_id}"
            if allow_empty:
                prompt_label += " (optional, press Enter to skip)"
            api_key = getpass.getpass(prompt_label + ": ")
        else:
            api_key = sys.stdin.read().strip()
    if not api_key and not allow_empty:
        _emit_error(
            "provider.set-key",
            "Empty API key.",
            as_json=as_json,
            exit_code=2,
        )

    metadata: dict[str, Any] = {}
    if model:
        metadata["model"] = model
    if base_url:
        metadata["base_url"] = base_url

    creds = ProviderCredentials(
        provider_id=provider_id,
        auth_type=AuthType.API_KEY,
        secret={"api_key": api_key},
        connected_at=now_iso(),
        metadata=metadata,
    )
    get_registry().store.set(creds)

    if no_test:
        _emit_set_key_result(
            provider_id, as_json=as_json, test_result=None, message="Saved."
        )
        return

    try:
        result = provider.test_connection()
    except Exception as exc:  # noqa: BLE001
        result = ProviderTestResult(ok=False, detail=str(exc))

    if not result.ok:
        # Record the failure breadcrumb but keep the key (the user may
        # have a transient network issue).
        creds.last_test_error = result.detail
        get_registry().store.set(creds)
    else:
        creds.verified_at = now_iso()
        get_registry().store.set(creds)

    _emit_set_key_result(
        provider_id,
        as_json=as_json,
        test_result=result,
        message=("Saved and verified." if result.ok else "Saved but probe failed."),
    )
    if not result.ok:
        raise SystemExit(1)


def _emit_set_key_result(
    provider_id: str,
    *,
    as_json: bool,
    test_result: ProviderTestResult | None,
    message: str,
) -> None:
    if as_json:
        emit_json(
            build_json_payload(
                command="provider.set-key",
                data={
                    "ok": test_result is None or test_result.ok,
                    "provider_id": provider_id,
                    "test_result": test_result.to_dict() if test_result else None,
                    "message": message,
                    "error": (
                        None
                        if test_result is None or test_result.ok
                        else test_result.detail
                    ),
                },
            )
        )
        return
    color = "green" if (test_result is None or test_result.ok) else "yellow"
    click.secho(message, fg=color)
    if test_result and test_result.detail:
        click.echo(f"  detail: {test_result.detail}")


# ---------------------------------------------------------------------------
# Note: there is no `provider login` subcommand. The CLI subprocess
# providers (claude-cli, codex-cli) handle their own auth via their
# own login commands -- run `claude login` or `codex login` directly
# in your shell. A future native OAuth provider that owns its own
# token storage would re-introduce a login flow here.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# disconnect
# ---------------------------------------------------------------------------


@provider_cmd.command("disconnect")
@click.argument("provider_id")
@click.option("--json", "as_json", is_flag=True)
def disconnect_cmd(provider_id: str, as_json: bool) -> None:
    """Forget the saved credential for a provider."""
    provider = _get_or_die(
        provider_id, as_json=as_json, command="provider.disconnect"
    )
    provider.disconnect()
    if as_json:
        emit_json(
            build_json_payload(
                command="provider.disconnect",
                data={"ok": True, "provider_id": provider_id},
            )
        )
    else:
        click.secho(f"Disconnected {provider_id}.", fg="green")


# ---------------------------------------------------------------------------
# use
# ---------------------------------------------------------------------------


@provider_cmd.command("use")
@click.argument("provider_id")
@click.option(
    "--fallback",
    default=None,
    help="Optional fallback provider id (or 'none' to clear).",
)
@click.option(
    "--model",
    default=None,
    help="Optionally update this provider's configured model in the same call.",
)
@click.option("--json", "as_json", is_flag=True)
def use_cmd(
    provider_id: str,
    fallback: str | None,
    model: str | None,
    as_json: bool,
) -> None:
    """Set the primary LLM provider in ``config/settings.yaml``."""
    _get_or_die(provider_id, as_json=as_json, command="provider.use")

    if fallback is not None and fallback not in ("", "none"):
        _get_or_die(fallback, as_json=as_json, command="provider.use")

    # Phase 17.9.10: optional --model lets a user say
    #   `autoapply provider use openai --model gpt-4.1`
    # without having to re-enter the API key via set-key.
    model_updated = False
    if model is not None:
        new_model = model.strip()
        creds = get_registry().store.get(provider_id)
        if creds is None:
            _emit_error(
                "provider.use",
                f"Cannot set model: {provider_id!r} is not connected. "
                f"Run `autoapply provider set-key {provider_id} --model {new_model}` first.",
                as_json=as_json,
                exit_code=2,
            )
        creds.metadata["model"] = new_model or None
        get_registry().store.set(creds)
        model_updated = True

    settings = _load_settings()
    llm = settings.setdefault("llm", {})
    llm["primary_provider"] = provider_id
    llm["provider"] = provider_id  # legacy alias kept for compat
    # Phase 11.1: keep the list and the scalar in sync so post-migrate
    # configs don't end up routing to a stale fallback (get_llm_settings
    # prefers the list when both are present).
    if fallback in ("", "none"):
        llm["fallback_provider"] = None
        llm["fallback_providers"] = []
        llm["allow_fallback"] = False
    elif fallback is not None:
        llm["fallback_provider"] = fallback
        llm["fallback_providers"] = [fallback]
        llm["allow_fallback"] = True

    _save_settings(settings)

    if as_json:
        emit_json(
            build_json_payload(
                command="provider.use",
                data={
                    "ok": True,
                    "primary_provider": provider_id,
                    "fallback_provider": llm.get("fallback_provider"),
                    "model_updated": model_updated,
                    "model": model if model_updated else None,
                },
            )
        )
    else:
        click.secho(f"Primary provider set to {provider_id}.", fg="green")
        if model_updated:
            click.echo(f"  model:    {model or '<cleared>'}")
        if llm.get("fallback_provider"):
            click.echo(f"  fallback: {llm['fallback_provider']}")


# ---------------------------------------------------------------------------
# set-model (Phase 17.9.10)
# ---------------------------------------------------------------------------


@provider_cmd.command("set-model")
@click.argument("provider_id")
@click.argument("model")
@click.option("--json", "as_json", is_flag=True)
def set_model_cmd(provider_id: str, model: str, as_json: bool) -> None:
    """Update the model for an already-connected provider.

    Same effect as `provider set-key --model X` but without prompting
    for a key. Useful for switching between catalog entries (gpt-4o-mini
    -> gpt-4.1) without re-entering credentials.
    """
    _get_or_die(provider_id, as_json=as_json, command="provider.set-model")
    creds = get_registry().store.get(provider_id)
    if creds is None:
        _emit_error(
            "provider.set-model",
            f"{provider_id!r} is not connected yet. Run "
            f"`autoapply provider set-key {provider_id} --model {model}` first.",
            as_json=as_json,
            exit_code=2,
        )
    previous = creds.metadata.get("model")
    new_model = model.strip()
    creds.metadata["model"] = new_model or None
    get_registry().store.set(creds)
    if as_json:
        emit_json(
            build_json_payload(
                command="provider.set-model",
                data={
                    "ok": True,
                    "provider_id": provider_id,
                    "model": new_model or None,
                    "previous_model": previous,
                },
            )
        )
    else:
        click.secho(
            f"{provider_id} model: {previous or '<default>'} -> "
            f"{new_model or '<default>'}",
            fg="green",
        )


# ---------------------------------------------------------------------------
# small-tier (Phase 17.9.10) -- mirrors the Settings UI knob.
# ---------------------------------------------------------------------------


@provider_cmd.group("small-tier")
def small_tier_group() -> None:
    """Configure the optional cheap-model tier for extraction calls."""


@small_tier_group.command("show")
@click.option("--json", "as_json", is_flag=True)
def small_tier_show_cmd(as_json: bool) -> None:
    """Print the currently-configured small_provider / small_model."""
    # Read directly from the sandbox-aware path so this works under
    # tests that monkeypatch `_SETTINGS_PATH`.
    raw = _load_settings()
    llm = raw.get("llm", {}) if isinstance(raw, dict) else {}
    small_provider = llm.get("small_provider")
    small_model = llm.get("small_model")
    if as_json:
        emit_json(
            build_json_payload(
                command="provider.small-tier.show",
                data={
                    "ok": True,
                    "small_provider": small_provider,
                    "small_model": small_model,
                },
            )
        )
        return
    if not small_provider and not small_model:
        click.echo("Small-tier: disabled (extraction calls go through primary).")
        return
    click.echo("Small-tier:")
    click.echo(f"  provider: {small_provider or '<unset>'}")
    click.echo(f"  model:    {small_model or '<provider default>'}")


@small_tier_group.command("set")
@click.argument("provider_id")
@click.option(
    "--model",
    default=None,
    help="Model id for the small tier (omit to use the provider's default).",
)
@click.option("--json", "as_json", is_flag=True)
def small_tier_set_cmd(
    provider_id: str, model: str | None, as_json: bool
) -> None:
    """Route extraction calls (JD parsing, resume import) through PROVIDER_ID."""
    from src.core.config import update_llm_settings  # noqa: PLC0415

    _get_or_die(provider_id, as_json=as_json, command="provider.small-tier.set")
    raw = _load_settings()
    llm = raw.get("llm", {}) if isinstance(raw, dict) else {}
    update_llm_settings(
        llm.get("primary_provider") or llm.get("provider") or "claude-cli",
        llm.get("fallback_provider"),
        bool(llm.get("allow_fallback")),
        config_path=_SETTINGS_PATH,
        small_provider=provider_id,
        small_model=(model or "").strip() or None,
        small_tier_action="set",
    )
    if as_json:
        emit_json(
            build_json_payload(
                command="provider.small-tier.set",
                data={
                    "ok": True,
                    "small_provider": provider_id,
                    "small_model": model or None,
                },
            )
        )
    else:
        click.secho(
            f"Small-tier: {provider_id}"
            + (f" (model={model})" if model else " (provider default)"),
            fg="green",
        )


@small_tier_group.command("clear")
@click.option("--json", "as_json", is_flag=True)
def small_tier_clear_cmd(as_json: bool) -> None:
    """Disable the small tier; extraction calls go back to the primary chain."""
    from src.core.config import update_llm_settings  # noqa: PLC0415

    raw = _load_settings()
    llm = raw.get("llm", {}) if isinstance(raw, dict) else {}
    update_llm_settings(
        llm.get("primary_provider") or llm.get("provider") or "claude-cli",
        llm.get("fallback_provider"),
        bool(llm.get("allow_fallback")),
        config_path=_SETTINGS_PATH,
        small_tier_action="clear",
    )
    if as_json:
        emit_json(
            build_json_payload(
                command="provider.small-tier.clear",
                data={"ok": True},
            )
        )
    else:
        click.secho("Small-tier cleared.", fg="green")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_or_die(
    provider_id: str, *, as_json: bool, command: str
) -> LLMProvider:
    registry = get_registry()
    instance = registry.maybe_get(provider_id)
    if instance is None:
        known = ", ".join(registry.ids())
        _emit_error(
            command,
            f"Unknown provider {provider_id!r}. Known: {known}",
            as_json=as_json,
            exit_code=2,
        )
    return instance  # type: ignore[return-value]  -- _emit_error never returns


def _emit_error(
    command: str,
    message: str,
    *,
    as_json: bool,
    exit_code: int,
    extra: dict[str, Any] | None = None,
) -> None:
    if as_json:
        data: dict[str, Any] = {"ok": False, "error": message}
        if extra:
            data.update(extra)
        emit_json(build_json_payload(command=command, data=data))
    else:
        click.secho(message, fg="red", err=True)
    raise SystemExit(exit_code)


def _load_settings() -> dict[str, Any]:
    if not _SETTINGS_PATH.exists():
        return {}
    with _SETTINGS_PATH.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise click.ClickException(
            f"{_SETTINGS_PATH} does not contain a mapping at the root."
        )
    return data


def _save_settings(data: dict[str, Any]) -> None:
    _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _SETTINGS_PATH.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, default_flow_style=False)
