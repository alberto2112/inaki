"""
UpdateGlobalLayerUseCase — escribe cambios en la capa global o global.secrets.

Regla de routing de capa:
- Si el campo pertenece a ``CAMPOS_SECRETS`` → escribe a ``global.secrets.yaml``.
- En cualquier otro caso → escribe a ``global.yaml``.

El llamador puede forzar la capa destino pasando ``layer`` explícitamente.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.ports.config_repository import LayerName

if TYPE_CHECKING:
    from core.ports.config_repository import IConfigRepository

# Rutas de campo que siempre se persisten en global.secrets.
# El campo ``providers.{key}.api_key`` se maneja en UpsertProviderUseCase.
CAMPOS_SECRETS: frozenset[str] = frozenset()


class UpdateGlobalLayerUseCase:
    """
    Actualiza uno o más campos de la capa global indicada.

    El caller puede especificar la capa de destino. Si no la especifica,
    la capa por defecto es ``LayerName.GLOBAL``.
    """

    def __init__(self, repo: "IConfigRepository") -> None:
        self._repo = repo

    def execute(
        self,
        cambios: dict[str, Any],
        layer: LayerName = LayerName.GLOBAL,
    ) -> None:
        """
        Escribe ``cambios`` en la capa ``layer``.

        Lee la capa actual, aplica los cambios (merge superficial sobre las
        secciones top-level modificadas) y persiste.

        Args:
            cambios: Dict con los campos a actualizar. El nivel top-level
                     debe coincidir con las secciones del YAML (``app``, ``llm``, etc.).
            layer: Capa destino. Solo ``GLOBAL`` o ``GLOBAL_SECRETS`` son válidos aquí.

        Raises:
            ValueError: Si se pasa una capa de agente.
        """
        if layer not in (LayerName.GLOBAL, LayerName.GLOBAL_SECRETS):
            raise ValueError(
                f"UpdateGlobalLayerUseCase solo acepta capas globales, recibió: {layer!r}"
            )

        datos_actuales = self._repo.read_layer(layer)
        datos_nuevos = _deep_merge(datos_actuales, cambios)
        self._repo.write_layer(layer, datos_nuevos)


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge recursivo campo a campo. Override tiene prioridad."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
