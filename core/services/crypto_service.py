"""Symmetric encryption service (Fernet).

Key lifecycle:
  1. Reads INAKI_SECRET_KEY from .env at project root.
  2. If absent, generates a new Fernet key, writes it to .env, and logs a warning.

Encrypted values are prefixed with "enc:" so callers can distinguish them from
plain-text values without extra metadata.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv, set_key

logger = logging.getLogger(__name__)

_ENC_PREFIX = "enc:"
_ENV_KEY = "INAKI_SECRET_KEY"


def _project_root() -> Path:
    # core/services/crypto_service.py → parents[2] = project root
    return Path(__file__).resolve().parents[2]


class CryptoService:
    """Fernet-based symmetric encryption. Key lives in INAKI_SECRET_KEY (.env)."""

    def __init__(self) -> None:
        self._fernet = Fernet(self._load_or_generate_key())

    # ------------------------------------------------------------------
    # Key management
    # ------------------------------------------------------------------

    def _load_or_generate_key(self) -> bytes:
        env_path = _project_root() / ".env"
        load_dotenv(env_path)
        key = os.getenv(_ENV_KEY, "").strip()
        if key:
            return key.encode()

        new_key = Fernet.generate_key()
        set_key(str(env_path), _ENV_KEY, new_key.decode())
        logger.warning(
            "INAKI_SECRET_KEY no encontrada — clave generada y guardada en %s. "
            "Guarda esta clave en un lugar seguro si necesitás recuperar datos cifrados.",
            env_path,
        )
        return new_key

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a string. Returns ``enc:<fernet_token>``."""
        token = self._fernet.encrypt(plaintext.encode()).decode()
        return f"{_ENC_PREFIX}{token}"

    def decrypt(self, value: str) -> str:
        """Decrypt an ``enc:<token>`` value. Plain values are returned unchanged."""
        if not self.is_encrypted(value):
            return value
        token = value[len(_ENC_PREFIX):]
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise ValueError(
                "No se pudo descifrar el valor. "
                "Verificá que INAKI_SECRET_KEY sea la misma que se usó para cifrar."
            ) from exc

    def is_encrypted(self, value: str) -> bool:
        """Return True if the value carries the enc: prefix."""
        return isinstance(value, str) and value.startswith(_ENC_PREFIX)
