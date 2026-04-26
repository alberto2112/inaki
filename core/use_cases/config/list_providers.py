"""
ListProvidersUseCase — lista los providers del registry sin exponer api_keys.

Combina la sección ``providers:`` de ``global.yaml`` con la de
``global.secrets.yaml`` (para detectar qué providers tienen api_key),
pero devuelve las entradas SIN el campo ``api_key``.

El propósito es poblar la pantalla de Providers en la TUI sin exponer
credenciales en la vista de lista.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.ports.config_repository import LayerName

if TYPE_CHECKING:
    from core.ports.config_repository import IConfigRepository


@dataclass(frozen=True)
class ProviderInfo:
    """Resumen de un provider del registry sin credenciales."""

    key: str
    """Nombre/clave del provider en el bloque ``providers:``."""

    type: str | None
    """Tipo de adapter (``None`` → se infiere de la key en runtime)."""

    base_url: str | None
    """URL base override. ``None`` si usa el default del adapter."""

    tiene_api_key: bool
    """Indica si el provider tiene ``api_key`` definida (en cualquier capa)."""


class ListProvidersUseCase:
    """Devuelve la lista de providers del registry sin exponer api_keys."""

    def __init__(self, repo: "IConfigRepository") -> None:
        self._repo = repo

    def execute(self) -> list[ProviderInfo]:
        """
        Retorna lista de ProviderInfo ordenada por key.

        Combina ambas capas globales para detectar presencia de api_key,
        pero NUNCA incluye el valor de la api_key en el resultado.
        """
        global_data = self._repo.read_layer(LayerName.GLOBAL)
        secrets_data = self._repo.read_layer(LayerName.GLOBAL_SECRETS)

        providers_base: dict = global_data.get("providers") or {}
        providers_secrets: dict = secrets_data.get("providers") or {}

        # Todas las keys que aparecen en cualquier capa
        todas_las_keys = set(providers_base) | set(providers_secrets)

        resultado: list[ProviderInfo] = []
        for key in sorted(todas_las_keys):
            entrada_base = providers_base.get(key) or {}
            entrada_secrets = providers_secrets.get(key) or {}

            tiene_api_key = bool(
                entrada_base.get("api_key") or entrada_secrets.get("api_key")
            )
            tipo = entrada_base.get("type") or entrada_secrets.get("type")
            base_url = entrada_base.get("base_url") or entrada_secrets.get("base_url")

            resultado.append(
                ProviderInfo(
                    key=key,
                    type=tipo,
                    base_url=base_url,
                    tiene_api_key=tiene_api_key,
                )
            )

        return resultado
