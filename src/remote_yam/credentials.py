"""Backend-only provider credential resolution for the local runner."""

from __future__ import annotations

import getpass
import os
import platform
import subprocess
from collections.abc import Callable, Mapping

PROVIDER_ENVIRONMENT_KEYS = {
    "openai": "OPENAI_API_KEY",
    "astra": "ASTRA_API_KEY",
}
KEYCHAIN_SERVICE = "blupe-public-yam"


class CredentialVault:
    """Hold provider credentials in backend process memory, never UI state."""

    def __init__(self, credentials: Mapping[str, str] | None = None) -> None:
        self._credentials = {
            provider: value
            for provider, value in (credentials or {}).items()
            if provider in PROVIDER_ENVIRONMENT_KEYS and value.strip()
        }

    def __repr__(self) -> str:
        configured = sorted(self._credentials)
        return f"CredentialVault(configured_providers={configured!r})"

    @classmethod
    def from_local_sources(
        cls,
        environment: Mapping[str, str] | None = None,
        keychain_lookup: Callable[[str], str | None] | None = None,
    ) -> "CredentialVault":
        environment = os.environ if environment is None else environment
        lookup = _macos_keychain_lookup if keychain_lookup is None else keychain_lookup
        values: dict[str, str] = {}
        for provider, variable in PROVIDER_ENVIRONMENT_KEYS.items():
            value = environment.get(variable, "").strip()
            if not value:
                value = (lookup(provider) or "").strip()
            if value:
                values[provider] = value
        return cls(values)

    def prompt_for(self, provider: str, prompt: Callable[[str], str] = getpass.getpass) -> None:
        self._validate_provider(provider)
        value = prompt(f"{provider.title()} API key (input hidden): ").strip()
        if not value:
            raise ValueError("Provider API key cannot be empty")
        self._credentials[provider] = value

    def require(self, provider: str) -> str:
        self._validate_provider(provider)
        value = self._credentials.get(provider)
        if value is None:
            variable = PROVIDER_ENVIRONMENT_KEYS[provider]
            raise RuntimeError(
                f"{provider.title()} credential is not configured; restart with a masked prompt, "
                f"OS keychain item, or {variable}"
            )
        return value

    def public_status(self) -> dict[str, bool]:
        return {
            provider: provider in self._credentials
            for provider in PROVIDER_ENVIRONMENT_KEYS
        }

    def clear(self, provider: str | None = None) -> None:
        targets = list(self._credentials) if provider is None else [provider]
        for target in targets:
            if target in self._credentials:
                self._credentials[target] = ""
                del self._credentials[target]

    @staticmethod
    def _validate_provider(provider: str) -> None:
        if provider not in PROVIDER_ENVIRONMENT_KEYS:
            raise ValueError(f"Unsupported provider: {provider}")


def _macos_keychain_lookup(provider: str) -> str | None:
    if platform.system() != "Darwin":
        return None
    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                KEYCHAIN_SERVICE,
                "-a",
                provider,
                "-w",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=3.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None
