"""Tests for the Phase 17.9.3 Ollama provider.

Ollama is the first provider whose ``api_key`` is allowed to be empty
(``allow_empty_key``), so this suite focuses on the keyless paths in
addition to the usual probe + catalog assertions.

The HTTP client is faked the same way ``test_providers_api.py`` fakes
OpenAI/Anthropic/Gemini -- no network at test time.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

import pytest

from src.providers.base import (
    AuthType,
    ProviderCredentials,
    ProviderError,
)
from src.providers.ollama import OllamaProvider
from src.providers.store import CredentialStore


class _FakeResponse:
    def __init__(self, status_code: int, body: Any | None = None) -> None:
        self.status_code = status_code
        self._body = body
        self.text = ""

    def json(self) -> Any:
        if self._body is None:
            raise ValueError("no body")
        return self._body


class _FakeClient(AbstractContextManager["_FakeClient"]):
    def __init__(self, *, response: _FakeResponse) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> _FakeResponse:
        self.calls.append({"method": "GET", "url": url, "headers": headers})
        return self.response


def _make_provider(
    tmp_path: Path,
    *,
    response: _FakeResponse,
    credentials: ProviderCredentials | None = None,
) -> tuple[OllamaProvider, _FakeClient, CredentialStore]:
    store = CredentialStore(path=tmp_path / "c.json")
    if credentials is not None:
        store.set(credentials)
    client = _FakeClient(response=response)
    provider = OllamaProvider(
        store=store, http_client_factory=lambda: client
    )
    return provider, client, store


class TestOllamaProvider:
    def test_class_metadata(self) -> None:
        assert OllamaProvider.id == "ollama"
        assert OllamaProvider.auth_type is AuthType.API_KEY
        assert OllamaProvider.allow_empty_key is True
        assert OllamaProvider.default_base_url.endswith("/v1")
        # Catalog is empty on purpose; runtime list comes from /api/tags.
        assert OllamaProvider.KNOWN_MODELS == ()

    def test_native_api_root_strips_v1_suffix(self, tmp_path: Path) -> None:
        provider, _, _ = _make_provider(
            tmp_path, response=_FakeResponse(200, {"models": []})
        )
        assert provider._native_api_root() == "http://localhost:11434/api"

    def test_native_api_root_honours_base_url_override(self, tmp_path: Path) -> None:
        creds = ProviderCredentials(
            provider_id="ollama",
            auth_type=AuthType.API_KEY,
            secret={},
            metadata={"base_url": "https://my-ollama.internal/v1"},
        )
        provider, _, _ = _make_provider(
            tmp_path,
            response=_FakeResponse(200, {"models": []}),
            credentials=creds,
        )
        assert provider._native_api_root() == "https://my-ollama.internal/api"

    def test_probe_uses_native_tags_endpoint(self, tmp_path: Path) -> None:
        provider, client, _ = _make_provider(
            tmp_path,
            response=_FakeResponse(
                200,
                {"models": [{"name": "llama3.2:latest"}, {"name": "qwen2.5:7b"}]},
            ),
        )
        result = provider._probe_connection("", timeout=5)
        assert result.ok
        assert result.model_count == 2
        assert client.calls[0]["url"].endswith("/api/tags")
        # No Authorization header when no key is configured.
        assert "Authorization" not in (client.calls[0]["headers"] or {})

    def test_probe_attaches_auth_when_key_present(self, tmp_path: Path) -> None:
        provider, client, _ = _make_provider(
            tmp_path,
            response=_FakeResponse(200, {"models": []}),
        )
        provider._probe_connection("sk-proxy-token", timeout=5)
        assert client.calls[0]["headers"]["Authorization"] == "Bearer sk-proxy-token"

    def test_probe_reports_server_down(self, tmp_path: Path) -> None:
        provider, _, _ = _make_provider(
            tmp_path, response=_FakeResponse(503)
        )
        result = provider._probe_connection("", timeout=5)
        assert not result.ok
        assert "503" in result.detail

    def test_get_api_key_returns_empty_when_unset(self, tmp_path: Path) -> None:
        # The keyless path: get_api_key MUST NOT raise even without
        # creds + env var, because allow_empty_key is True.
        provider, _, _ = _make_provider(
            tmp_path,
            response=_FakeResponse(200, {"models": []}),
        )
        # Ensure no creds row.
        assert provider.credentials() is None
        assert provider.get_api_key() == ""

    def test_is_configured_after_credential_row(self, tmp_path: Path) -> None:
        provider, _, store = _make_provider(
            tmp_path,
            response=_FakeResponse(200, {"models": []}),
        )
        assert not provider.is_configured()
        # Empty secret is fine; the row itself is the "yes I set this up" signal.
        store.set(
            ProviderCredentials(
                provider_id="ollama",
                auth_type=AuthType.API_KEY,
                secret={},
            )
        )
        assert provider.is_configured()

    def test_connect_accepts_empty_key(self, tmp_path: Path) -> None:
        # connect() must persist a credential row even when api_key="".
        provider, _, store = _make_provider(
            tmp_path, response=_FakeResponse(200, {"models": []})
        )
        result = provider.connect("", model="llama3.2:latest")
        assert result.ok
        creds = store.get("ollama")
        assert creds is not None
        assert creds.secret == {"api_key": ""} or creds.secret == {}
        assert creds.metadata.get("model") == "llama3.2:latest"
        assert creds.verified_at  # populated on successful probe

    def test_connect_still_rejects_non_string(self, tmp_path: Path) -> None:
        provider, _, _ = _make_provider(
            tmp_path, response=_FakeResponse(200, {"models": []})
        )
        with pytest.raises(ProviderError):
            provider.connect(123)  # type: ignore[arg-type]

    def test_list_local_models_returns_ids(self, tmp_path: Path) -> None:
        provider, _, _ = _make_provider(
            tmp_path,
            response=_FakeResponse(
                200,
                {
                    "models": [
                        {"name": "llama3.2:latest"},
                        {"name": "qwen2.5-coder:7b"},
                        # Malformed entry is skipped, not crashed.
                        {"missing_name": True},
                    ]
                },
            ),
        )
        assert provider.list_local_models() == [
            "llama3.2:latest",
            "qwen2.5-coder:7b",
        ]

    def test_list_local_models_returns_empty_on_error(self, tmp_path: Path) -> None:
        provider, _, _ = _make_provider(
            tmp_path, response=_FakeResponse(500)
        )
        assert provider.list_local_models() == []
