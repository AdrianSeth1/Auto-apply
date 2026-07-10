"""Shared base for API-key REST providers.

OpenAI, Anthropic, and Gemini all follow the same shape:

    1. user pastes an API key
    2. we hit a cheap endpoint to verify it
    3. on success we persist the key + a verified_at timestamp
    4. generation calls a chat-completion endpoint

The differences are URLs, headers, and JSON shapes -- captured by the
concrete subclasses. Connection management, persistence, and
:meth:`disconnect` live here so each subclass stays narrow.

The HTTP client is injected via ``http_client_factory`` so tests can
substitute a fake without monkeypatching httpx globals.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

import httpx

from src.providers.base import (
    AuthType,
    LLMProvider,
    ProviderCredentials,
    ProviderError,
    ProviderTestResult,
    now_iso,
)

logger = logging.getLogger("autoapply.providers.api")

# A factory returning a context-managed HTTP client. We accept any
# callable returning something ``with`` -friendly so tests can hand in
# a stub that doesn't speak the real httpx protocol.
HttpClientFactory = Callable[[], AbstractContextManager[httpx.Client]]


def _default_http_client_factory(timeout: float) -> HttpClientFactory:
    def _factory() -> AbstractContextManager[httpx.Client]:
        return httpx.Client(timeout=timeout)

    return _factory


class ApiKeyProvider(LLMProvider):
    """Base class for ``api_key`` auth-type providers."""

    auth_type = AuthType.API_KEY

    # Subclasses set these.
    api_key_env_var: str = ""
    default_model: str = ""
    # Phase 17.9.3: some "API-key" providers don't actually require one.
    # Ollama, a self-hosted vLLM, or an LM Studio endpoint typically
    # serves without auth -- a credential record still needs to exist
    # so we can persist a base_url override and a verified_at
    # breadcrumb, but the secret may be empty. Setting this flag opts
    # the provider into the keyless connect / generate paths in
    # ``connect()`` / ``get_api_key()`` and the application + CLI
    # layers so the user isn't forced to type a fake key.
    allow_empty_key: bool = False
    # Phase 17.9.13: soft client-side key format hints. The probe
    # remains the canonical validator (formats drift -- OpenAI added
    # `sk-proj-`, `sk-svcacct-` etc. after `sk-`); we surface these
    # only so the UI can show "looks like a key for a different
    # provider" before burning a network round-trip. Both are
    # optional; an empty pattern disables the hint.
    api_key_pattern: str = ""
    api_key_example: str = ""

    def __init__(
        self,
        store: Any | None = None,
        *,
        http_client_factory: HttpClientFactory | None = None,
    ) -> None:
        super().__init__(store)
        # Defer creating the default factory until first call so ctor
        # cost stays trivial; this matters for the registry which
        # instantiates every provider on first lookup.
        self._http_client_factory = http_client_factory

    # ----- credential plumbing -----

    def get_api_key(self) -> str:
        """Return the configured API key, falling back to env vars.

        Raises :class:`ProviderError` if neither is set, **unless**
        :attr:`allow_empty_key` is True (Ollama / self-hosted servers
        that typically run without auth) -- in that case we return the
        empty string and the subclass's ``_headers`` is responsible
        for omitting the Authorization header.
        """
        creds = self.credentials()
        if creds and creds.secret.get("api_key"):
            return str(creds.secret["api_key"])
        if self.api_key_env_var:
            env_value = os.environ.get(self.api_key_env_var)
            if env_value:
                return env_value
        if self.allow_empty_key:
            return ""
        raise ProviderError(
            f"{self.display_name or self.id} is not connected. "
            f"Run `autoapply provider connect {self.id}` or set "
            f"{self.api_key_env_var or '<env var>'}."
        )

    def get_model(self) -> str:
        """Return the configured chat model, falling back to ``default_model``."""
        creds = self.credentials()
        if creds:
            override = creds.metadata.get("model")
            if isinstance(override, str) and override.strip():
                return override.strip()
        return self.default_model

    def connect(
        self,
        api_key: str,
        *,
        model: str | None = None,
        timeout: int = 10,
    ) -> ProviderTestResult:
        """Verify the candidate key with a probe; persist only on success.

        Mirrors the OAuth pattern -- never store an unverified
        credential. The probe uses :meth:`_probe_connection` directly
        so we don't have to first ``set`` then ``delete`` on failure.

        When :attr:`allow_empty_key` is True (Ollama / self-hosted
        servers without auth), an empty key is accepted -- the
        credential row still gets persisted so we can record a custom
        base_url and a verified_at breadcrumb.
        """
        if not isinstance(api_key, str):
            raise ProviderError("API key must be a string.")
        api_key = api_key.strip()
        if not api_key and not self.allow_empty_key:
            raise ProviderError("API key must be a non-empty string.")
        try:
            result = self._probe_connection(api_key, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 -- network/HTTP boundary
            return ProviderTestResult(ok=False, detail=_redact(str(exc), api_key))

        if not result.ok:
            return result

        creds = ProviderCredentials(
            provider_id=self.id,
            auth_type=self.auth_type,
            secret={"api_key": api_key},
            connected_at=now_iso(),
            verified_at=now_iso(),
            metadata={"model": model} if model else {},
        )
        if self._store is None:
            raise ProviderError(
                "Cannot persist credentials -- provider was constructed without a store."
            )
        self._store.set(creds)
        return result

    def test_connection(self, *, timeout: int = 10) -> ProviderTestResult:
        try:
            api_key = self.get_api_key()
        except ProviderError as exc:
            return ProviderTestResult(ok=False, detail=str(exc))
        try:
            result = self._probe_connection(api_key, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            result = ProviderTestResult(ok=False, detail=_redact(str(exc), api_key))

        creds = self.credentials()
        if creds is not None and self._store is not None:
            creds.verified_at = now_iso() if result.ok else creds.verified_at
            creds.last_test_error = None if result.ok else result.detail
            self._store.set(creds)
        return result

    # ----- subclass hooks -----

    def _probe_connection(self, api_key: str, *, timeout: int) -> ProviderTestResult:
        """Hit a cheap endpoint and report success/failure.

        Subclasses override. Implementations MUST NOT log the raw key
        and MUST translate provider-specific HTTP errors into a
        user-readable :class:`ProviderTestResult.detail`.
        """
        raise NotImplementedError

    def generate(
        self,
        prompt: str,
        *,
        system: str = "",
        timeout: int = 120,
        output_format: str = "text",  # noqa: ARG002 -- subclass hook
        model: str | None = None,  # noqa: ARG002 -- subclass hook
    ) -> str:
        """Default impl: subclasses override.

        Kept abstract here rather than raising NotImplementedError so
        type checkers flag the missing override at subclass-time.
        """
        raise NotImplementedError

    # ----- helpers -----

    def _client(self, timeout: float) -> AbstractContextManager[httpx.Client]:
        factory = self._http_client_factory or _default_http_client_factory(timeout)
        return factory()

    @staticmethod
    def _measure(start: float) -> int:
        return int((time.monotonic() - start) * 1000)


def _redact(message: str, secret: str) -> str:
    """Strip a known secret out of a message before it reaches the user."""
    if not secret:
        return message
    return message.replace(secret, "***")


# ---------------------------------------------------------------------------
# OpenAI-compatible provider (Phase 17.9)
# ---------------------------------------------------------------------------


class OpenAICompatibleProvider(ApiKeyProvider):
    """Base for providers speaking the OpenAI chat-completions REST shape.

    Phase 17.9 extracts what was previously hard-coded in
    :class:`src.providers.openai.OpenAIProvider` so DeepSeek, OpenRouter,
    Moonshot, Qwen, xAI, Groq, Mistral, and the like can each be a
    ~10-line subclass that only differs in id / display_name / base URL
    / default model / KNOWN_MODELS.

    Subclasses set:

    * :attr:`default_base_url` -- the `/v1`-style root, no trailing slash.
    * :attr:`api_key_env_var`  -- env var for fallback.
    * :attr:`default_model`    -- fallback model id.
    * :attr:`KNOWN_MODELS`     -- curated catalog (optional).
    * :attr:`user_agent`       -- override only when an upstream demands it.

    The probe hits ``GET {base}/models`` (universal across compatible
    providers) and generation hits ``POST {base}/chat/completions``.
    Auth header is ``Authorization: Bearer <key>`` -- providers that
    deviate (e.g. Anthropic's ``x-api-key``) get their own class.
    """

    # Subclasses override.
    default_base_url: str = ""
    user_agent: str = "autoapply/0.7"

    def _base_url(self) -> str:
        creds = self.credentials()
        if creds:
            override = creds.metadata.get("base_url")
            if isinstance(override, str) and override.strip():
                return override.strip().rstrip("/")
        return self.default_base_url.rstrip("/")

    def _headers(self, api_key: str) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": self.user_agent,
        }
        key = api_key.strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def _probe_connection(
        self, api_key: str, *, timeout: int
    ) -> ProviderTestResult:
        import time as _time  # local import keeps the module's top-level lean  # noqa: PLC0415

        url = f"{self._base_url()}/models"
        t0 = _time.monotonic()
        with self._client(timeout) as client:
            response = client.get(url, headers=self._headers(api_key))
        latency = self._measure(t0)

        if response.status_code == 401:
            return ProviderTestResult(
                ok=False,
                detail=(
                    f"{self.display_name or self.id} rejected the key (401). "
                    "Check the value and try again."
                ),
                latency_ms=latency,
            )
        if response.status_code >= 400:
            return ProviderTestResult(
                ok=False,
                detail=(
                    f"{self.display_name or self.id} returned HTTP "
                    f"{response.status_code}: {_safe_text(response)}"
                ),
                latency_ms=latency,
            )

        body: Any
        try:
            body = response.json()
        except ValueError:
            body = {}
        models = body.get("data") if isinstance(body, dict) else None
        return ProviderTestResult(
            ok=True,
            detail="OK",
            latency_ms=latency,
            model_count=len(models) if isinstance(models, list) else None,
        )

    def generate(
        self,
        prompt: str,
        *,
        system: str = "",
        timeout: int = 120,
        output_format: str = "text",  # noqa: ARG002 -- JSON is prompt-driven
        model: str | None = None,
    ) -> str:
        # Late import: avoid pulling ProviderError / ProviderErrorKind at module
        # load time and keep this module symmetrical with anthropic.py / gemini.py
        # which import them locally too.
        import httpx as _httpx  # noqa: PLC0415

        from src.providers.base import (  # noqa: PLC0415
            ProviderError,
            ProviderErrorKind,
            classify_http_status,
        )

        api_key = self.get_api_key()
        # Phase 17.9.5: caller may override the model for tiered dispatch
        # (e.g. "small" tier). Fall back to the configured model otherwise.
        model = (model or "").strip() or self.get_model()
        url = f"{self._base_url()}/chat/completions"
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        # 2026-07-09: hard output cap. Without max_tokens a thinking-mode
        # local model was observed decoding 40k+ tokens through repeated
        # context shifts (losing its instructions each shift) until the
        # HTTP timeout fired — one call burned 4+ GPU-minutes to produce
        # garbage. No legitimate artifact in this codebase needs more
        # than a few thousand output tokens.
        payload = {"model": model, "messages": messages, "max_tokens": 4096}

        try:
            with self._client(timeout) as client:
                response = client.post(
                    url, headers=self._headers(api_key), json=payload
                )
        except _httpx.TimeoutException as exc:
            raise ProviderError(
                f"{self.display_name or self.id} generation timed out after {timeout}s",
                kind=ProviderErrorKind.TIMEOUT,
            ) from exc
        except _httpx.HTTPError as exc:
            raise ProviderError(
                f"{self.display_name or self.id} network error: {exc}",
                kind=ProviderErrorKind.NETWORK,
            ) from exc

        if response.status_code >= 400:
            raise ProviderError(
                f"{self.display_name or self.id} generation failed "
                f"(HTTP {response.status_code}): {_safe_text(response)}",
                kind=classify_http_status(response.status_code),
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderError(
                f"{self.display_name or self.id} response was not JSON: {exc}",
                kind=ProviderErrorKind.PARSE,
            ) from exc

        choices = body.get("choices") if isinstance(body, dict) else None
        if not isinstance(choices, list) or not choices:
            raise ProviderError(
                f"{self.display_name or self.id} response missing 'choices': {body!r}",
                kind=ProviderErrorKind.PARSE,
            )
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise ProviderError(
                f"{self.display_name or self.id} response missing "
                f"'choices[0].message.content': {body!r}",
                kind=ProviderErrorKind.PARSE,
            )
        return content


def _safe_text(response: httpx.Response, limit: int = 240) -> str:
    text = (response.text or "").strip()
    return text[:limit] + ("…" if len(text) > limit else "")
