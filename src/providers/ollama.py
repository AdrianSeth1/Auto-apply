"""Ollama provider (local LLM server, OpenAI-compatible).

Ollama exposes both its native ``/api/*`` surface and an OpenAI-shaped
``/v1/chat/completions``; we use the OpenAI-compat path for generation
(so the ``OpenAICompatibleProvider`` base does the heavy lifting) and
the native ``/api/tags`` for both connection probing and the dynamic
model catalog (17.9.4).

Auth: Ollama typically runs without authentication on ``localhost:11434``.
We opt into the keyless path via :attr:`ApiKeyProvider.allow_empty_key`
so the connect / generate flow accepts an empty secret -- a credential
record still gets persisted so the user can override the ``base_url``
(for remote / reverse-proxied Ollama instances) and so the health
monitor has a verified_at breadcrumb to render.

Catalog: ``KNOWN_MODELS`` is empty on purpose. Ollama's model surface
is whatever the user has run ``ollama pull`` for; 17.9.4 will fetch
the runtime list from ``/api/tags`` and merge it into the picker.
"""

from __future__ import annotations

import time

from src.providers.api_base import OpenAICompatibleProvider
from src.providers.base import ProviderTestResult


class OllamaProvider(OpenAICompatibleProvider):
    id = "ollama"
    display_name = "Ollama (local)"
    description = (
        "Local Ollama server -- any model the user has pulled via `ollama pull`"
    )
    install_hint = "Install Ollama from https://ollama.com, then `ollama serve`"
    # Rarely needed (only if Ollama is fronted by an auth proxy).
    api_key_env_var = "OLLAMA_API_KEY"
    # 127.0.0.1, NOT localhost (repo invariant #9): on this Windows
    # setup localhost resolves IPv6-first and Ollama listens on IPv4
    # only, so every probe/generation call paid an IPv6 connect
    # attempt before falling back — repeated stalls on the web
    # process's event loop during provider health checks.
    default_base_url = "http://127.0.0.1:11434/v1"
    # `llama3.2` is the cheapest "you almost certainly have it" default;
    # users will overwhelmingly override this via the picker.
    default_model = "llama3.2"
    allow_empty_key = True
    # Runtime catalog comes from /api/tags -- see catalog API in 17.9.4.
    KNOWN_MODELS = ()

    # ----- helpers -----

    def generate(
        self,
        prompt: str,
        *,
        system: str = "",
        timeout: int = 120,
        output_format: str = "text",
        model: str | None = None,
    ) -> str:
        """OpenAI-compat generate with Qwen3 thinking-mode suppression.

        2026-07-09: Qwen3's ``/no_think`` soft switch is only honored in
        the USER turn — placing it in the system prompt (our first
        attempt) did nothing, and thinking-mode output was burning
        thousands of hidden tokens per call: cover letters came back
        "too short" (the visible text under the think stream) and one
        call decoded 40k+ tokens through repeated context shifts.
        Appending the switch to the user prompt for qwen3-family models
        disables thinking per-turn; other models never see it.
        """
        resolved = (model or "").strip() or self.get_model()
        if resolved.lower().startswith("qwen3"):
            prompt = f"{prompt}\n\n/no_think"
        return super().generate(
            prompt,
            system=system,
            timeout=timeout,
            output_format=output_format,
            model=resolved,
        )

    def _native_api_root(self) -> str:
        """Return the native (non-/v1) Ollama API root.

        Ollama hosts the OpenAI-compat layer under ``/v1`` and the native
        management endpoints under ``/api``. We need both:

        * Generation goes through ``/v1/chat/completions`` (inherited).
        * Probing and catalog discovery use native ``/api/tags`` because
          some Ollama builds don't return a useful response for
          ``GET /v1/models`` when no model is loaded yet.
        """
        base = self._base_url()
        if base.endswith("/v1"):
            base = base[: -len("/v1")]
        return base.rstrip("/") + "/api"

    def _headers(self, api_key: str) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": self.user_agent,
        }
        # Only attach the auth header when the user actually configured
        # a key -- a stray ``Authorization: Bearer `` confuses some
        # reverse proxies.
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    # ----- LLMProvider / ApiKeyProvider overrides -----

    def is_configured(self) -> bool:
        # A credential row is the user's signal of "I have set Ollama up".
        # An empty secret is fine for keyless local servers; the row may
        # carry a custom base_url and a verified_at timestamp.
        return self.credentials() is not None

    def _probe_connection(
        self, api_key: str, *, timeout: int
    ) -> ProviderTestResult:
        url = f"{self._native_api_root()}/tags"
        t0 = time.monotonic()
        with self._client(timeout) as client:
            response = client.get(url, headers=self._headers(api_key))
        latency = self._measure(t0)

        if response.status_code >= 400:
            return ProviderTestResult(
                ok=False,
                detail=(
                    f"Ollama returned HTTP {response.status_code} from "
                    f"{url}. Is `ollama serve` running?"
                ),
                latency_ms=latency,
            )
        try:
            body = response.json()
        except ValueError:
            body = {}
        models = body.get("models") if isinstance(body, dict) else None
        return ProviderTestResult(
            ok=True,
            detail=(
                f"OK ({len(models)} model(s) installed)"
                if isinstance(models, list)
                else "OK"
            ),
            latency_ms=latency,
            model_count=len(models) if isinstance(models, list) else None,
        )

    def list_local_models(self, *, timeout: int = 10) -> list[str]:
        """Return the model ids the local Ollama daemon has pulled.

        Used by the 17.9.4 catalog API to populate the picker; surfaced
        as a method so tests / scripts can call it directly without
        going through the HTTP route. Returns an empty list on any
        failure -- callers shouldn't break when the server is down.
        """
        try:
            api_key = self.get_api_key()
        except Exception:  # noqa: BLE001
            api_key = ""
        url = f"{self._native_api_root()}/tags"
        try:
            with self._client(timeout) as client:
                response = client.get(url, headers=self._headers(api_key))
        except Exception:  # noqa: BLE001
            return []
        if response.status_code >= 400:
            return []
        try:
            body = response.json()
        except ValueError:
            return []
        models = body.get("models") if isinstance(body, dict) else None
        if not isinstance(models, list):
            return []
        # Ollama returns entries like ``{"name": "llama3.2:latest", ...}``;
        # the picker just needs the bare id.
        return [
            entry["name"]
            for entry in models
            if isinstance(entry, dict) and isinstance(entry.get("name"), str)
        ]
