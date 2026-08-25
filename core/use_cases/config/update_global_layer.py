"""
UpdateGlobalLayerUseCase — escribe cambios en la capa global (``global.yaml``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.ports.config_repository import LayerName
from core.use_cases.config._merge import (
    deep_merge_con_eliminaciones,
    resolver_tristados,
)

if TYPE_CHECKING:
    from core.ports.config_repository import IConfigRepository


class UpdateGlobalLayerUseCase:
    """
    Actualiza uno o más campos de la capa global.
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

        Lee la capa actual, aplica un merge recursivo con los cambios (soporta
        ``CampoTriestado(INHERIT)`` para ELIMINAR una clave, igual que el carril
        de agente) y persiste.

        Args:
            cambios: Dict con los campos a actualizar. El nivel top-level
                     debe coincidir con las secciones del YAML (``app``, ``llm``, etc.).
                     Un valor ``CampoTriestado(TristadoValor.INHERIT)`` elimina
                     esa clave del YAML.
            layer: Capa destino. Solo ``GLOBAL`` es válido aquí.

        Raises:
            ValueError: Si se pasa una capa de agente.
        """
        if layer is not LayerName.GLOBAL:
            raise ValueError(
                f"UpdateGlobalLayerUseCase solo acepta la capa global, recibió: {layer!r}"
            )

        datos_actuales = self._repo.read_layer(layer)
        datos_resueltos = resolver_tristados(cambios)
        datos_nuevos = deep_merge_con_eliminaciones(datos_actuales, datos_resueltos)
        self._repo.write_layer(layer, datos_nuevos)
