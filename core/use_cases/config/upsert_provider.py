"""
UpsertProviderUseCase — crea o actualiza un provider en el registry.

Todos los campos (``type``, ``base_url``, ``api_key``) van a ``global.yaml``:
la capa ya es privada (permisos 600) desde que los ``*.secrets.yaml`` dejaron
de existir. Lo que marca a ``api_key`` como credencial es el schema
(``kind == "secret"``), que enmascara su valor en la UI.

Si el provider ya existe, solo se actualizan los campos provistos.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.ports.config_repository import LayerName

if TYPE_CHECKING:
    from core.ports.config_repository import IConfigRepository


class UpsertProviderUseCase:
    """
    Crea o actualiza un provider en el registry global (``global.yaml``).
    """

    def __init__(self, repo: "IConfigRepository") -> None:
        self._repo = repo

    def execute(
        self,
        key: str,
        type: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        """
        Upsert del provider ``key``.

        Args:
            key: Nombre del provider en el registry (ej: ``"groq"``, ``"openai"``).
            type: Tipo de adapter. ``None`` → no se escribe (usa el existente o el default).
            base_url: URL base override. ``None`` → no se escribe.
            api_key: Credencial. ``None`` → no se modifica.

        Nota: pasar ``api_key=""`` vacío equivale a no pasarla (se ignora).
        """
        datos_globales = self._repo.read_layer(LayerName.GLOBAL)
        providers_globales: dict = datos_globales.get("providers") or {}
        entrada_global: dict = dict(providers_globales.get(key) or {})

        if type is not None:
            entrada_global["type"] = type
        if base_url is not None:
            entrada_global["base_url"] = base_url
        if api_key:
            entrada_global["api_key"] = api_key

        providers_globales[key] = entrada_global
        datos_globales["providers"] = providers_globales
        self._repo.write_layer(LayerName.GLOBAL, datos_globales)
