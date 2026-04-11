"""Web search configuration store.

Persists Tavily credentials in ``~/.inaki/config/web_search_config.yaml``.
Only the ``api_key`` field is encrypted (prefixed with ``enc:``).
All other fields are stored as plain text so the file remains human-readable.

YAML layout example::

    # Iñaki — Web Search configuration
    # El campo api_key está cifrado. No lo edites manualmente.

    api_key: "enc:gAAAAABh..."
    search_depth: basic
    max_results: 5
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from core.services.crypto_service import CryptoService

_SENSITIVE_FIELDS: frozenset[str] = frozenset({"api_key"})
_CONFIG_FILENAME = "web_search_config.yaml"
_HEADER = (
    "# Iñaki — Web Search configuration\n"
    "# El campo api_key está cifrado. No lo edites manualmente.\n\n"
)


def _config_dir() -> Path:
    config = Path.home() / ".inaki" / "config"
    config.mkdir(parents=True, exist_ok=True)
    return config


class WebSearchConfigStore:
    """Reads and writes ``web_search_config.yaml`` with selective field encryption."""

    def __init__(self, crypto: CryptoService) -> None:
        self._crypto = crypto
        self._path = _config_dir() / _CONFIG_FILENAME

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> dict[str, Any]:
        """Return config dict with sensitive fields decrypted. Empty dict if no file."""
        if not self._path.exists():
            return {}
        with self._path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return self._decrypt_fields(data)

    def save(self, data: dict[str, Any]) -> None:
        """Encrypt sensitive fields and write YAML. Merges with existing config."""
        current = self.load()
        merged = {**current, **{k: v for k, v in data.items() if v not in (None, "")}}
        to_write = self._encrypt_fields(merged)
        with self._path.open("w", encoding="utf-8") as f:
            f.write(_HEADER)
            yaml.dump(to_write, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    def exists(self) -> bool:
        return self._path.exists()

    def masked(self) -> dict[str, Any]:
        """Return config with sensitive fields masked. Reads raw file (no decryption)."""
        if not self._path.exists():
            return {}
        with self._path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        result = dict(data)
        for field in _SENSITIVE_FIELDS:
            if result.get(field):
                result[field] = "***"
        return result

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _encrypt_fields(self, data: dict[str, Any]) -> dict[str, Any]:
        result = dict(data)
        for field in _SENSITIVE_FIELDS:
            val = result.get(field)
            if val and isinstance(val, str) and not self._crypto.is_encrypted(val):
                result[field] = self._crypto.encrypt(val)
        return result

    def _decrypt_fields(self, data: dict[str, Any]) -> dict[str, Any]:
        result = dict(data)
        for field in _SENSITIVE_FIELDS:
            val = result.get(field)
            if val and isinstance(val, str):
                result[field] = self._crypto.decrypt(val)
        return result
