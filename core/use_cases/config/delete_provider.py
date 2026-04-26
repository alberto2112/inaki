"""
DeleteProviderUseCase — elimina un provider del registry.

Siempre elimina la entrada de ``global.yaml``.
La ``api_key`` en ``global.secrets.yaml`` se elimina solo si ``borrar_api_key=True``.
La TUI confirma con el usuario antes de pasar ``borrar_api_key=True``.
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

    def execute(self, key: str, borrar_api_key: bool = False) -> None:
        """
        Elimina el provider ``key`` del registry.

        Args:
            key: Nombre del provider a eliminar (ej: ``"groq"``).
            borrar_api_key: Si ``True``, también elimina la entrada de ``global.secrets.yaml``.
                             Por defecto ``False`` — la TUI pregunta al usuario primero.

        Nota: Si el provider no existe en una de las capas, esa capa se ignora
        silenciosamente (operación idempotente por capa).
        """
        # Eliminar de global.yaml
        datos_globales = self._repo.read_layer(LayerName.GLOBAL)
        providers_globales: dict = dict(datos_globales.get("providers") or {})
        providers_globales.pop(key, None)
        datos_globales["providers"] = providers_globales
        self._repo.write_layer(LayerName.GLOBAL, datos_globales)

        # Eliminar api_key de secrets solo si se pide explícitamente
        if borrar_api_key:
            datos_secrets = self._repo.read_layer(LayerName.GLOBAL_SECRETS)
            providers_secrets: dict = dict(datos_secrets.get("providers") or {})
            providers_secrets.pop(key, None)
            datos_secrets["providers"] = providers_secrets
            self._repo.write_layer(LayerName.GLOBAL_SECRETS, datos_secrets)
