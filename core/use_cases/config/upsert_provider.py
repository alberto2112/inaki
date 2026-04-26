"""
UpsertProviderUseCase — crea o actualiza un provider en el registry.

Reglas de routing de capa:
- ``api_key`` → SIEMPRE a ``global.secrets.yaml`` (nunca a ``global.yaml``).
- ``type``, ``base_url`` → a ``global.yaml``.

Si el provider ya existe, solo se actualizan los campos provistos.
Si es nuevo, se crea la entrada en la capa correspondiente.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.ports.config_repository import LayerName

if TYPE_CHECKING:
    from core.ports.config_repository import IConfigRepository


class UpsertProviderUseCase:
    """
    Crea o actualiza un provider en el registry global.

    Garantiza que ``api_key`` nunca se persista en ``global.yaml``:
    siempre va a ``global.secrets.yaml``.
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
            api_key: Credencial. ``None`` → no se modifica. Si se provee → va a secrets.

        Nota: pasar ``api_key=""`` vacío equivale a no pasarla (se ignora).
        """
        # Actualizar global.yaml con campos no-secret
        datos_globales = self._repo.read_layer(LayerName.GLOBAL)
        providers_globales: dict = datos_globales.get("providers") or {}
        entrada_global: dict = dict(providers_globales.get(key) or {})

        if type is not None:
            entrada_global["type"] = type
        if base_url is not None:
            entrada_global["base_url"] = base_url

        # Remover api_key de global.yaml si por error estuviera allí
        entrada_global.pop("api_key", None)

        providers_globales[key] = entrada_global
        datos_globales["providers"] = providers_globales
        self._repo.write_layer(LayerName.GLOBAL, datos_globales)

        # api_key siempre va a secrets
        if api_key:
            datos_secrets = self._repo.read_layer(LayerName.GLOBAL_SECRETS)
            providers_secrets: dict = datos_secrets.get("providers") or {}
            entrada_secrets: dict = dict(providers_secrets.get(key) or {})
            entrada_secrets["api_key"] = api_key
            providers_secrets[key] = entrada_secrets
            datos_secrets["providers"] = providers_secrets
            self._repo.write_layer(LayerName.GLOBAL_SECRETS, datos_secrets)
