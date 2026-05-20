"""LLM CLI wrapper + registry bridge.

Invokes Claude Code CLI and Codex CLI via subprocess. As of Phase 10.5
this module also bridges to the provider registry so the same
``generate_text`` entry point can dispatch to API-key providers
(OpenAI / Anthropic / Gemini) without changing call-sites.

Dispatch order:

1. If the configured provider id is registered AND configured in the
   registry (``LLMProvider.is_configured``), call its ``generate``.
2. Otherwise, fall back to the legacy CLI helpers below.

The fallback path is what kept Phase 1-9 working before the registry
existed; we intentionally keep it so users who haven't gone through
the new `autoapply provider` flow see no regression.

Phase 11.1 extends the dispatch with:

* An ordered fallback chain (``fallback_providers: [a, b, c]``) on top
  of the legacy scalar ``fallback_provider`` setting.
* Error classification: only transient kinds (auth/quota/network/timeout/
  server) advance to the next provider; bad-request / parse errors stop
  immediately because retrying a malformed prompt on a second provider
  just burns money on the same failure.
* An attempt chain side-channeled via a :class:`~contextvars.ContextVar`
  so the agent loop can attach it to each :class:`AgentStep` trace.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from src.core.config import load_config

logger = logging.getLogger("autoapply.llm")

# Provider ids that the legacy subprocess dispatcher knows how to
# handle. The registry may know more ids than this -- those are
# dispatched via :func:`_dispatch_via_registry` instead.
_LEGACY_CLI_PROVIDERS = ("claude-cli", "codex-cli")

# Backwards-compatible export used elsewhere in the codebase (tests +
# config validation). Now includes registry-only providers so a
# settings.yaml entry like ``primary_provider: openai`` is accepted.
SUPPORTED_PROVIDERS = _LEGACY_CLI_PROVIDERS


class LLMError(Exception):
    """Raised when an LLM call fails after the configured fallback chain.

    ``attempts`` is the per-provider record of the dispatch attempts that
    led to this failure. It mirrors the value of :data:`last_attempt_chain`
    at the moment the error was raised and is included here so callers
    can surface it without reaching for the ContextVar.
    """

    def __init__(self, message: str, *, attempts: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.attempts = attempts or []


# Side-channel: ``generate_text`` writes the per-provider attempt list
# here on every call (success or failure). The agent loop reads it after
# each LLM round-trip and stores a copy on the AgentStep so the trace
# captures which provider actually answered. Using a ContextVar keeps
# concurrent callers isolated even under asyncio fan-out.
last_attempt_chain: contextvars.ContextVar[list[dict[str, Any]]] = (
    contextvars.ContextVar("autoapply_llm_last_attempts", default=[])
)


def detect_available_providers() -> dict[str, bool]:
    """Detect which supported LLM CLIs are available in PATH."""
    return {
        "claude-cli": _resolve_executable("claude") is not None,
        "codex-cli": _resolve_executable("codex") is not None,
    }


def get_llm_settings(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return normalized LLM settings with backward-compatible defaults.

    Two fallback shapes are accepted, in this priority order:

    1. ``fallback_providers: [a, b, c]`` -- ordered list, Phase 11.1+.
    2. ``fallback_provider: a`` -- single fallback, pre-Phase-11.1.

    The normalised output exposes both ``fallback_provider`` (first entry
    of the chain, for back-compat with callers that only knew one) and
    ``fallback_providers`` (the full deduplicated chain).
    """
    if config is None:
        config = load_config()

    llm = config.get("llm", {})
    primary = llm.get("primary_provider") or llm.get("provider") or "claude-cli"

    # Pull the new list shape first so a user who set both gets the list.
    raw_chain = llm.get("fallback_providers")
    if isinstance(raw_chain, str):
        # Permit a comma-separated string in settings.yaml or env overrides.
        raw_chain = [item.strip() for item in raw_chain.split(",") if item.strip()]
    if not isinstance(raw_chain, list):
        raw_chain = []

    legacy_single = llm.get("fallback_provider")
    if legacy_single and not raw_chain:
        raw_chain = [legacy_single]

    timeout = int(llm.get("timeout", 120))
    primary = _normalize_provider(primary, role="primary")

    chain: list[str] = []
    seen = {primary}
    for entry in raw_chain:
        if entry in (None, "", "none"):
            continue
        normalised = _normalize_provider(str(entry), role="fallback")
        if normalised in seen:
            continue
        seen.add(normalised)
        chain.append(normalised)

    allow_fallback_raw = llm.get("allow_fallback")
    if allow_fallback_raw is None:
        allow_fallback = bool(chain)
    else:
        allow_fallback = bool(allow_fallback_raw)

    # Phase 17.9.5: optional small-tier routing. Both are independent
    # of the primary chain so users can put a cheap fast model behind
    # the same primary provider, or route to an entirely different
    # provider (e.g. primary=anthropic, small=groq) for extraction-
    # style work where accuracy / creativity matters less.
    small_provider_raw = llm.get("small_provider")
    small_provider = (
        _normalize_provider(str(small_provider_raw), role="small")
        if isinstance(small_provider_raw, str) and small_provider_raw.strip()
        else None
    )
    small_model_raw = llm.get("small_model")
    small_model = (
        str(small_model_raw).strip()
        if isinstance(small_model_raw, str) and small_model_raw.strip()
        else None
    )

    return {
        "primary_provider": primary,
        "fallback_provider": chain[0] if chain else None,
        "fallback_providers": chain,
        "allow_fallback": allow_fallback,
        "timeout": timeout,
        "small_provider": small_provider,
        "small_model": small_model,
    }


def _resolve_provider_fingerprint_inputs(
    provider_id: str,
) -> tuple[str | None, str | None]:
    """Return ``(model, base_url)`` for the registered provider.

    Both are semantic inputs to the LLM call: changing the model on
    an API-key provider produces a different response; pointing the
    same provider id at a different ``base_url`` (a compatible API
    endpoint, an OpenAI-shaped proxy, etc.) also does. The cache
    fingerprint must include both so a config change invalidates
    cached entries cleanly.

    Both return ``None`` for subprocess providers (where AutoApply
    has no knob), as well as on any registry-side hiccup.
    """
    try:
        from src.providers import get_registry  # noqa: PLC0415

        instance = get_registry().maybe_get(provider_id)
        if instance is None:
            return None, None
        getter = getattr(instance, "get_model", None)
        if getter is not None:
            model = getter() or None
        else:
            model = getattr(instance, "default_model", None) or None
        base_url_getter = getattr(instance, "_base_url", None)
        if callable(base_url_getter):
            try:
                base_url = base_url_getter() or None
            except Exception:  # noqa: BLE001 -- never let provider quirks break LLM
                base_url = None
        else:
            base_url = None
        return model, base_url
    except Exception:  # noqa: BLE001 -- registry hiccup must not break LLM call
        return None, None


def _cache_fingerprint(
    *,
    primary_provider: str,
    system: str,
    prompt: str,
    output_format: str,
    model: str | None,
    base_url: str | None,
) -> str:
    """SHA256 fingerprint over the LLM inputs that determine the output.

    The fingerprint deliberately excludes:
      * ``timeout`` (a circuit-breaker, not a semantic parameter)
      * ``fallback_providers`` (we never cache fallback responses
        under the primary's key; see ``generate_text``)
      * provider-side stochasticity (model temperature isn't surfaced
        to ``generate_text`` today; if/when it is, add it here AND bump
        ``CACHE_VERSION`` so existing entries are invalidated)

    The fingerprint INCLUDES:
      * ``model`` -- changing the model on an API-key provider is
        a semantic input change (codex review P2).
      * ``base_url`` -- the same provider id can point at different
        compatible endpoints / proxies, which produce different
        responses (also codex review P2).

    Both are ``None`` for subprocess providers where AutoApply has
    no model/URL knob.

    JSON encoding with ``sort_keys`` keeps the digest stable across
    dict-iteration orderings.
    """
    payload = json.dumps(
        {
            "provider": primary_provider,
            "model": model,
            "base_url": base_url,
            "system": system,
            "prompt": prompt,
            "output_format": output_format,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def generate_text(
    prompt: str,
    *,
    system: str = "",
    timeout: int | None = None,
    output_format: str = "text",
    config: dict[str, Any] | None = None,
    cache: bool = False,
    tier: str = "primary",
) -> str:
    """Generate text using the configured provider chain.

    Behaviour:

    * The chain is ``[primary, *fallback_providers]``; fallbacks are only
      consulted when ``allow_fallback`` is true.
    * Each provider is tried in order. On failure, we inspect the
      :class:`~src.providers.base.ProviderErrorKind`: transient kinds
      (auth / quota / network / timeout / server / unknown) advance to the
      next provider; non-transient kinds (bad request, parse) abort the
      whole call because retrying a malformed prompt elsewhere won't help.
    * The full attempt list is written to :data:`last_attempt_chain` and
      attached to the raised :class:`LLMError` so the agent loop / trace
      viewer can show what was tried.

    Phase 12.4: ``cache=True`` consults the L1+L2 cache before
    dispatching. The cache key is a SHA256 fingerprint over the
    primary provider id + system + prompt + output_format; see
    :func:`_cache_fingerprint`. ``cache`` defaults to ``False`` so
    the agent loop (which makes one-shot reasoning calls where stale
    answers would be wrong) is opt-in to caching; deterministic
    retrieval call sites should pass ``cache=True`` to benefit from
    Phase 12.6/12.7's hit-rate + $-saved telemetry. Only successful
    responses are cached -- transient failures do not poison the
    cache.
    """
    settings = get_llm_settings(config)
    timeout = timeout or settings["timeout"]

    # Phase 17.9.5: tier resolution. Default ("primary") preserves the
    # existing behaviour exactly: primary provider first, then the
    # configured fallback chain. The "small" tier swaps in the
    # ``small_provider`` (or stays on primary if only ``small_model``
    # is set) and threads ``small_model`` as a per-call model override.
    # When neither knob is configured, "small" silently behaves as
    # "primary" so call sites can opt in optimistically.
    model_override: str | None = None
    model_override_provider: str | None = None
    if tier == "small":
        model_override = settings.get("small_model")
        small_provider = settings.get("small_provider")
        model_override_provider = small_provider or settings["primary_provider"]
        if small_provider:
            providers = [small_provider]
            # The small tier still gets the fallback chain as a safety
            # net, but with the small_provider promoted to head and
            # de-duplicated against the rest.
            for entry in (
                [settings["primary_provider"], *settings["fallback_providers"]]
                if settings["allow_fallback"]
                else [settings["primary_provider"]]
            ):
                if entry not in providers:
                    providers.append(entry)
        else:
            providers = [settings["primary_provider"]]
            if settings["allow_fallback"]:
                providers.extend(settings["fallback_providers"])
    elif tier == "primary":
        providers = [settings["primary_provider"]]
        if settings["allow_fallback"]:
            providers.extend(settings["fallback_providers"])
    else:
        raise LLMError(
            f"Unknown LLM tier {tier!r}. Expected 'primary' or 'small'."
        )

    attempts: list[dict[str, Any]] = []
    last_attempt_chain.set(attempts)
    fatal_kind = None

    # The provider whose answer we'd store under -- always the head of
    # the configured chain for this tier (small_provider for "small"
    # mode, primary_provider otherwise). The fingerprint and the
    # "only cache when this provider answered" guard both use it.
    primary_id = providers[0]

    cache_key: str | None = None
    if cache:
        provider_model, base_url = _resolve_provider_fingerprint_inputs(
            primary_id
        )
        # Phase 17.9.5: the small-tier model override is part of what
        # determines the response, so fold it into the cache key. Without
        # this, primary and small-tier requests with otherwise identical
        # prompt+provider would collide and serve each other's cached
        # answers.
        effective_model = model_override or provider_model
        cache_key = _cache_fingerprint(
            primary_provider=primary_id,
            system=system,
            prompt=prompt,
            output_format=output_format,
            model=effective_model,
            base_url=base_url,
        )
        try:
            from src.cache import get_cache  # noqa: PLC0415

            cached_value = get_cache().get("llm", cache_key)
        except Exception as exc:  # noqa: BLE001 -- cache must never break LLM calls
            logger.debug("Cache lookup skipped (%s).", exc)
            cached_value = None
        if cached_value is not None:
            # Record a synthetic attempt so the trace viewer / cost
            # dashboard can see the call was served from cache rather
            # than hitting a provider. ``kind='cache_hit'`` is a
            # convention shared with the upcoming Phase 12.7 dashboard.
            attempts.append(
                {
                    "provider": primary_id,
                    "ok": True,
                    "kind": "cache_hit",
                    "error": None,
                    "latency_ms": 0,
                    "cached": True,
                }
            )
            return cached_value

    # Phase 18.5: every dispatch is funnelled through the global +
    # per-provider rate-limit gate so concurrent task fan-out (e.g.
    # asyncio.gather over bullet rewrites) can't multiply into
    # provider abuse.
    from src.utils.parallelism import llm_call_gate  # noqa: PLC0415

    for provider in providers:
        start = time.monotonic()
        attempt: dict[str, Any] = {
            "provider": provider,
            "ok": False,
            "kind": None,
            "error": None,
            "latency_ms": 0,
        }
        attempts.append(attempt)
        try:
            provider_model = model_override if provider == model_override_provider else None
            with llm_call_gate(provider):
                result = _call_provider(
                    provider,
                    prompt,
                    system=system,
                    timeout=timeout,
                    output_format=output_format,
                    model=provider_model,
                )
        except LLMError as exc:
            attempt["latency_ms"] = int((time.monotonic() - start) * 1000)
            kind = _attempt_kind(exc)
            attempt["kind"] = kind.value
            attempt["error"] = str(exc)
            logger.warning(
                "LLM provider %s failed (%s): %s", provider, kind.value, exc
            )
            if not kind.is_transient:
                fatal_kind = kind
                break
            continue
        attempt["latency_ms"] = int((time.monotonic() - start) * 1000)
        attempt["ok"] = True
        attempt["cached"] = False
        # Phase 12.4: only cache successful responses, only when the
        # caller opted in, AND only when the primary itself answered.
        # Caching a fallback response under the primary's key would
        # keep replaying the fallback's answer even after the primary
        # recovered, because future identical calls would short-
        # circuit on the cache before retrying the primary.
        if cache and cache_key is not None and provider == primary_id:
            try:
                from src.cache import get_cache  # noqa: PLC0415

                get_cache().set("llm", cache_key, result)
            except Exception as exc:  # noqa: BLE001 -- cache failures must not break the call
                logger.debug("Cache write skipped (%s).", exc)
        return result

    error_lines = [
        f"{a['provider']} [{a['kind'] or 'no-kind'}]: {a['error']}"
        for a in attempts
        if not a["ok"]
    ]
    summary = (
        "LLM call aborted on non-transient error from "
        f"{attempts[-1]['provider']}: {fatal_kind.value if fatal_kind else 'unknown'}"
        if fatal_kind is not None
        else "All configured LLM providers failed"
    )
    raise LLMError(
        f"{summary}. " + " | ".join(error_lines),
        attempts=list(attempts),
    )


def generate_json(
    prompt: str,
    *,
    system: str = "",
    timeout: int | None = None,
    config: dict[str, Any] | None = None,
    cache: bool = False,
    tier: str = "primary",
) -> Any:
    """Generate JSON-like output using the configured provider order.

    Phase 12.4: ``cache=True`` opts in to the L1+L2 cache (see
    :func:`generate_text`). The cache stores the raw text response;
    the JSON parse happens on every call so a corrupted cache entry
    is still caught by the parser rather than masquerading as the
    parsed value.

    Phase 17.9.5: ``tier='small'`` opts into the optional cheap-model
    route configured under ``llm.small_provider`` / ``llm.small_model``.
    """
    raw = generate_text(
        prompt,
        system=system,
        timeout=timeout,
        output_format="json",
        config=config,
        cache=cache,
        tier=tier,
    )
    return _parse_json_response(raw)


def claude_generate(
    prompt: str,
    *,
    system: str = "",
    timeout: int = 120,
    output_format: str = "text",
) -> str:
    """Call Claude Code CLI directly for text generation."""
    executable = _resolve_executable("claude")
    if executable is None:
        raise LLMError(
            "Claude CLI not found. Install with: npm install -g @anthropic-ai/claude-code"
        )

    # Phase 17.x: invoking ``claude`` from inside the AutoApply project
    # tree makes the CLI auto-discover this repo (CLAUDE.md, AGENTS.md,
    # hooks, .git, and the per-project memory under ``~/.claude/
    # projects/<cwd-hash>/``). The CLI then slips into its default
    # "coding agent in this repo" persona, our resume-parser system
    # prompt gets drowned out, and the model asks "What would you like
    # me to work on in ``C:\\Projects\\AutoApply``?" instead of
    # returning YAML.
    #
    # We tried ``--bare`` (the CLI's documented minimal-inference
    # mode) but on 2.1.133 it changes Anthropic auth to
    # ``ANTHROPIC_API_KEY``-only -- subscription users lose auth and
    # the CLI either errors or returns canned greetings. The mode
    # also appears to drop the positional ``[prompt]`` argument
    # entirely.
    #
    # The fix that survives all of that: keep the CLI in its normal
    # mode (so OAuth / keychain auth still works) and just hand the
    # subprocess an empty scratch directory as cwd. The CLI then has
    # no project tree to discover, the per-project memory hash points
    # at an empty dir, and user-level config under ``$HOME`` continues
    # to load normally for auth. No env vars touched, no impact on
    # the API-based providers (which never spawn a subprocess).
    cmd = [executable, "--print", "--output-format", output_format]
    if system:
        cmd.extend(["--system-prompt", system])
    cmd.append(prompt)

    logger.debug(
        "Claude CLI call: prompt=%d chars, system=%d chars", len(prompt), len(system)
    )

    try:
        with tempfile.TemporaryDirectory(prefix="claude-cli-cwd-") as scratch_cwd:
            result = subprocess.run(
                cmd,
                cwd=scratch_cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
            )
    except subprocess.TimeoutExpired:
        raise LLMError(f"Claude CLI timed out after {timeout}s")
    except FileNotFoundError as exc:
        raise LLMError(
            "Claude CLI not found. Install with: npm install -g @anthropic-ai/claude-code"
        ) from exc

    if result.returncode != 0:
        raise LLMError(f"Claude CLI error (code {result.returncode}): {result.stderr.strip()}")

    response = result.stdout.strip()
    logger.debug("Claude CLI response: %d chars", len(response))
    return response


def claude_generate_json(prompt: str, *, system: str = "", timeout: int = 120) -> Any:
    """Call Claude CLI directly and parse the response as JSON."""
    raw = claude_generate(prompt, system=system, timeout=timeout, output_format="json")
    return _parse_json_response(raw)


def codex_generate(
    prompt: str,
    *,
    system: str = "",
    timeout: int = 120,
    output_format: str = "text",
) -> str:
    """Call Codex CLI directly for text generation."""
    executable = _resolve_executable("codex")
    if executable is None:
        raise LLMError("Codex CLI not found. Install with: npm install -g @openai/codex")

    full_prompt = prompt
    if system:
        full_prompt = f"System instructions:\n{system}\n\nUser request:\n{prompt}"
    if output_format == "json":
        full_prompt = f"{full_prompt}\n\nReturn only valid JSON with no markdown fences."

    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as output_file:
        output_path = Path(output_file.name)
    output_path.unlink(missing_ok=True)

    cmd = [
        executable,
        "exec",
        "--full-auto",
        "--color",
        "never",
        "--output-last-message",
        str(output_path),
        full_prompt,
    ]

    logger.debug("Codex CLI call: prompt=%d chars, system=%d chars", len(prompt), len(system))

    # Phase 17.x: same cwd-isolation rationale as ``claude_generate``.
    # Codex CLI auto-discovers AGENTS.md and project state from the
    # cwd tree -- running from the AutoApply repo lets that project
    # context override our prompt. A fresh empty cwd keeps the CLI
    # in pure-inference mode. User-level Codex auth under ``$HOME``
    # is untouched.
    try:
        with tempfile.TemporaryDirectory(prefix="codex-cli-cwd-") as scratch_cwd:
            result = subprocess.run(
                cmd,
                cwd=scratch_cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
            )
    except subprocess.TimeoutExpired:
        output_path.unlink(missing_ok=True)
        raise LLMError(f"Codex CLI timed out after {timeout}s")
    except FileNotFoundError as exc:
        output_path.unlink(missing_ok=True)
        raise LLMError("Codex CLI not found. Install with: npm install -g @openai/codex") from exc

    try:
        final_message = (
            output_path.read_text(encoding="utf-8").strip() if output_path.exists() else ""
        )
    finally:
        output_path.unlink(missing_ok=True)

    if result.returncode != 0:
        raise LLMError(f"Codex CLI error (code {result.returncode}): {result.stderr.strip()}")

    response = final_message or result.stdout.strip()
    logger.debug("Codex CLI response: %d chars", len(response))
    return response


def _attempt_kind(exc: LLMError) -> Any:
    """Return the :class:`ProviderErrorKind` recorded on a wrapped error.

    The registry dispatch path raises ``LLMError(...) from ProviderError``
    so we walk the ``__cause__`` chain. Errors that didn't come through
    a provider (e.g. the legacy CLI helpers raising directly) classify
    as ``UNKNOWN`` -- transient -- so behaviour matches pre-Phase-11.1.
    """
    from src.providers.base import ProviderError, ProviderErrorKind  # noqa: PLC0415

    cause = exc.__cause__
    if isinstance(cause, ProviderError):
        return cause.kind
    # Legacy CLI helpers (claude_generate / codex_generate) raise
    # LLMError directly without a ProviderError cause. Use the text-based
    # classifier so timeouts / auth issues still classify correctly.
    from src.providers.base import classify_cli_error  # noqa: PLC0415

    inferred = classify_cli_error(str(exc))
    return inferred if inferred is not ProviderErrorKind.UNKNOWN else ProviderErrorKind.UNKNOWN


def _call_provider(
    provider: str,
    prompt: str,
    *,
    system: str,
    timeout: int,
    output_format: str,
    model: str | None = None,
) -> str:
    """Dispatch to the selected provider.

    Tries the Phase 10 provider registry first; falls back to the
    legacy hard-wired CLI dispatch for the two original providers
    (``claude-cli`` / ``codex-cli``) so behaviour is unchanged for
    users who haven't enrolled a provider via the registry yet.

    ``model`` is the Phase 17.9.5 per-call override forwarded from
    ``generate_text`` when the small-tier knob is active. Subprocess
    providers ignore it (their CLI pins the model via its own auth).
    """
    if _dispatch_via_registry_enabled():
        registry_result = _dispatch_via_registry(
            provider,
            prompt,
            system=system,
            timeout=timeout,
            output_format=output_format,
            model=model,
        )
        if registry_result is not None:
            return registry_result

    if provider == "claude-cli":
        return claude_generate(prompt, system=system, timeout=timeout, output_format=output_format)
    if provider == "codex-cli":
        return codex_generate(prompt, system=system, timeout=timeout, output_format=output_format)
    raise LLMError(f"Unsupported LLM provider: {provider}")


def _dispatch_via_registry_enabled() -> bool:
    """Module-level toggle so tests can disable the registry path."""
    return True


def _dispatch_via_registry(
    provider: str,
    prompt: str,
    *,
    system: str,
    timeout: int,
    output_format: str,
    model: str | None = None,
) -> str | None:
    """Try to satisfy the request from the provider registry.

    Returns ``None`` when the registry can't (provider unknown or not
    configured), letting the caller fall back to the legacy CLI path.
    Raises :class:`LLMError` when the provider IS configured but the
    call fails -- bubbling that up preserves the existing
    primary/fallback semantics in ``generate_text``.
    """
    try:
        from src.providers import get_registry  # noqa: PLC0415
        from src.providers.base import ProviderError  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 -- import errors must not break callers
        logger.debug("Provider registry unavailable: %s", exc)
        return None

    try:
        registry = get_registry()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Provider registry init failed: %s", exc)
        return None

    instance = registry.maybe_get(provider)
    if instance is None:
        # Provider id not registered at all -- fall back to legacy.
        return None
    if not instance.is_configured():
        # Registered but the user hasn't run `autoapply provider
        # connect`. For legacy CLI providers, this is the normal
        # case -- silently fall back so behaviour matches Phase 9.
        return None

    try:
        raw = instance.generate(
            prompt,
            system=system,
            timeout=timeout,
            output_format=output_format,
            model=model,
        )
    except ProviderError as exc:
        # Re-raise as LLMError so generate_text's fallback loop
        # records the error and tries the next provider.
        raise LLMError(f"Provider {provider!r} failed: {exc}") from exc

    # Provider already honoured the output_format hint (CLI providers
    # via --output-format, API providers via prompt) so just return.
    return raw


def _normalize_provider(provider: str, *, role: str) -> str:
    """Validate a provider string.

    Accepts legacy CLI ids and any id registered with the Phase 10
    provider registry so a ``primary_provider: openai`` setting works
    without explicit config-layer additions.
    """
    if provider in SUPPORTED_PROVIDERS:
        return provider
    if _provider_id_is_known(provider):
        return provider
    supported = ", ".join(sorted({*SUPPORTED_PROVIDERS, *_known_registry_ids()}))
    raise ValueError(
        f"Invalid {role} LLM provider '{provider}'. Expected one of: {supported}"
    )


def _provider_id_is_known(provider_id: str) -> bool:
    return provider_id in _known_registry_ids()


def _known_registry_ids() -> tuple[str, ...]:
    """Best-effort introspection of the registry; never raises."""
    try:
        from src.providers import get_registry  # noqa: PLC0415

        return tuple(get_registry().ids())
    except Exception:  # noqa: BLE001
        return ()


def _resolve_executable(command: str) -> str | None:
    """Resolve a CLI executable path for direct subprocess use.

    On Windows, passing bare commands like ``codex`` to ``subprocess.run`` may fail
    even when ``shutil.which`` can see a ``.cmd`` shim. Using the resolved path avoids
    that CreateProcess lookup issue.
    """
    return shutil.which(command) or shutil.which(f"{command}.cmd") or shutil.which(f"{command}.exe")


def _parse_json_response(raw: str) -> Any:
    """Parse JSON output and tolerate fenced responses."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = [line for line in cleaned.split("\n") if not line.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return raw

    if isinstance(parsed, dict) and "result" in parsed:
        inner = parsed["result"]
        if isinstance(inner, str):
            try:
                return json.loads(inner)
            except (json.JSONDecodeError, TypeError):
                return inner
    return parsed
