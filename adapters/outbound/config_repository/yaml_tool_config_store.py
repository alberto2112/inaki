"""YamlToolConfigStore — IToolConfigStore sobre global.secrets.yaml.

Persiste el bloque ``tool_config:`` dentro de ``global.secrets.yaml`` (capa 2
del merge de 4 capas), preservando el resto del archivo y sus comentarios
(ruamel). Los campos sensibles se cifran con Fernet y prefijo ``enc:`` — la
sensibilidad queda codificada en la representación, así ``masked()`` no
necesita conocer qué campos son sensibles.

La clave Fernet vive en ``~/.inaki/secret.key`` (archivo plano, 0600). Se
auto-genera en el primer ``set`` con campos sensibles. ADVERTENCIA honesta
sobre el modelo de amenaza: clave y datos comparten disco — el cifrado
protege contra divulgación accidental del YAML (backups compartidos, ``git
add`` por error), NO contra un atacante con acceso al filesystem.

Si la clave cambia (o se pierde), los valores ``enc:`` existentes dejan de
ser descifrables: ``get()`` los omite con WARNING y la tool vuelve a pedir
configuración — el usuario re-configura desde el chat y listo.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from ruamel.yaml import YAML

from core.ports.outbound.tool_config_port import IToolConfigStore

logger = logging.getLogger(__name__)

_ENC_PREFIX = "enc:"
_MASK = "***"


class YamlToolConfigStore(IToolConfigStore):
    """Store del Tool Config Protocol respaldado por global.secrets.yaml."""

    def __init__(
        self,
        secrets_path: Path,
        key_path: Path,
        initial: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """``initial`` es la vista mergeada de ``tool_config`` post-load (4 capas),
        con los valores sensibles aún cifrados (``enc:``) tal como están en disco.
        """
        self._secrets_path = secrets_path
        self._key_path = key_path
        self._fernet: Fernet | None = None
        self._data: dict[str, dict[str, Any]] = {
            ns: dict(valores) for ns, valores in (initial or {}).items()
        }
        self._yaml = YAML()
        self._yaml.preserve_quotes = True

    # ------------------------------------------------------------------
    # IToolConfigStore
    # ------------------------------------------------------------------

    def get(self, namespace: str) -> dict[str, Any]:
        resultado: dict[str, Any] = {}
        for campo, valor in self._data.get(namespace, {}).items():
            if isinstance(valor, str) and valor.startswith(_ENC_PREFIX):
                descifrado = self._decrypt(valor)
                if descifrado is None:
                    logger.warning(
                        "tool_config.%s.%s: no se pudo descifrar (¿cambió secret.key?) — "
                        "campo omitido, re-configurá desde el chat",
                        namespace,
                        campo,
                    )
                    continue
                resultado[campo] = descifrado
            else:
                resultado[campo] = valor
        return resultado

    def set(
        self,
        namespace: str,
        values: dict[str, Any],
        sensitive: frozenset[str] = frozenset(),
    ) -> None:
        actual = self._data.setdefault(namespace, {})
        for campo, valor in values.items():
            if valor in (None, ""):
                continue
            if campo in sensitive and isinstance(valor, str):
                valor = _ENC_PREFIX + self._get_fernet().encrypt(valor.encode()).decode()
            actual[campo] = valor
        self._persistir()

    def masked(self, namespace: str) -> dict[str, Any]:
        return {
            campo: _MASK if isinstance(valor, str) and valor.startswith(_ENC_PREFIX) else valor
            for campo, valor in self._data.get(namespace, {}).items()
        }

    # ------------------------------------------------------------------
    # Cifrado
    # ------------------------------------------------------------------

    def _get_fernet(self) -> Fernet:
        if self._fernet is None:
            self._fernet = Fernet(self._load_or_generate_key())
        return self._fernet

    def _load_or_generate_key(self) -> bytes:
        if self._key_path.exists():
            return self._key_path.read_bytes().strip()
        key = Fernet.generate_key()
        self._key_path.parent.mkdir(parents=True, exist_ok=True)
        self._key_path.write_bytes(key)
        os.chmod(self._key_path, 0o600)
        logger.warning(
            "Clave de cifrado generada en %s — hacé backup: sin ella las "
            "credenciales cifradas no se recuperan",
            self._key_path,
        )
        return key

    def _decrypt(self, valor: str) -> str | None:
        try:
            return self._get_fernet().decrypt(valor[len(_ENC_PREFIX) :].encode()).decode()
        except (InvalidToken, ValueError, OSError):
            return None

    # ------------------------------------------------------------------
    # Persistencia
    # ------------------------------------------------------------------

    def _persistir(self) -> None:
        """Reescribe SOLO el bloque tool_config del secrets.yaml (resto intacto)."""
        documento: dict[str, Any] = {}
        if self._secrets_path.exists():
            with self._secrets_path.open("r", encoding="utf-8") as f:
                documento = self._yaml.load(f) or {}

        documento["tool_config"] = {ns: dict(vals) for ns, vals in self._data.items()}

        self._secrets_path.parent.mkdir(parents=True, exist_ok=True)
        # Write atómico: tmp en el mismo dir + os.replace (evita secrets a medias).
        fd, tmp_str = tempfile.mkstemp(
            dir=self._secrets_path.parent, prefix=".tool_config_", suffix=".yaml"
        )
        tmp = Path(tmp_str)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                self._yaml.dump(documento, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._secrets_path)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        logger.info("tool_config persistido en %s", self._secrets_path)
