"""
ListProvidersUseCase — lista los providers del registry sin exponer api_keys.

Lee la sección ``providers:`` de ``global.yaml`` y devuelve las entradas
SIN el campo ``api_key`` (solo un booleano indicando si está definida).

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
    """Indica si el provider tiene ``api_key`` definida."""


class ListProvidersUseCase:
    """Devuelve la lista de providers del registry sin exponer api_keys."""

    def __init__(self, repo: "IConfigRepository") -> None:
        self._repo = repo

    def execute(self) -> list[ProviderInfo]:
        """
        Retorna lista de ProviderInfo ordenada por key.

        NUNCA incluye el valor de la api_key en el resultado.
        """
        global_data = self._repo.read_layer(LayerName.GLOBAL)
        providers: dict = global_data.get("providers") or {}

        resultado: list[ProviderInfo] = []
        for key in sorted(providers):
            entrada = providers.get(key) or {}
            resultado.append(
                ProviderInfo(
                    key=key,
                    type=entrada.get("type"),
                    base_url=entrada.get("base_url"),
                    tiene_api_key=bool(entrada.get("api_key")),
                )
            )

        return resultado
