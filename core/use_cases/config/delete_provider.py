"""
DeleteProviderUseCase — elimina un provider del registry.

Elimina la entrada completa de ``global.yaml``, credencial incluida: desde la
erradicación de los ``*.secrets.yaml`` la ``api_key`` vive en la misma entrada,
así que no hay forma (ni sentido) de borrar el provider dejándola huérfana.
La TUI confirma con el usuario antes de llamar.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.ports.config_repository import LayerName

if TYPE_CHECKING:
    from core.ports.config_repository import IConfigRepository


class DeleteProviderUseCase:
    """Elimina un provider del registry global."""

    def __init__(self, repo: "IConfigRepository") -> None:
        self._repo = repo

    def execute(self, key: str) -> None:
        """
        Elimina el provider ``key`` del registry (con su ``api_key``).

        Args:
            key: Nombre del provider a eliminar (ej: ``"groq"``).

        Nota: si el provider no existe, es no-op (idempotente).
        """
        datos_globales = self._repo.read_layer(LayerName.GLOBAL)
        providers_globales: dict = dict(datos_globales.get("providers") or {})
        providers_globales.pop(key, None)
        datos_globales["providers"] = providers_globales
        self._repo.write_layer(LayerName.GLOBAL, datos_globales)
