"""Puerto del Tool Config Protocol — configuración de tools desde el canal.

Protocolo estándar para tools que permiten al usuario configurar credenciales
conversando con el agente (operation=configure), sin editar YAML a mano.

Contrato para una tool que adopta el protocolo:

1. Declara ``config_namespace`` (class attr de ``ITool``) con un slug único
   (ej: ``"web_search"``, ``"exchange"``). El container le inyecta el store
   como kwarg ``config_store`` en el constructor — esto aplica también a
   tools de extensiones (``ext/``), cuyo contrato de instanciación pasa de
   ``tool_cls()`` a ``tool_cls(config_store=...)`` cuando declaran namespace.
2. Expone ``operation=configure`` (persiste campos via ``set``) y
   ``operation=show_config`` (muestra via ``masked``) en su schema.
3. Sin credenciales, devuelve un error "CONFIGURATION REQUIRED" que instruye
   al LLM a pedírselas al usuario y reinvocar con ``configure``.

Los valores viven en el bloque ``tool_config.{namespace}`` de
``global.secrets.yaml`` (sistema de 4 capas — editable a mano también). Los
campos declarados sensibles se cifran en reposo con prefijo ``enc:``; el
``get`` los devuelve descifrados de forma transparente.

Los métodos son SINCRÓNICOS a propósito: operan sobre un dict en memoria y
archivos YAML de pocos KB, y los consumidores incluyen engines sincrónicos
(ej: exchangelib) donde un puerto async obligaría a gimnasia de event-loop.
"""

from abc import ABC, abstractmethod
from typing import Any


class IToolConfigStore(ABC):
    """Lectura/escritura del bloque de configuración de una tool."""

    @abstractmethod
    def get(self, namespace: str) -> dict[str, Any]:
        """Config del namespace con campos sensibles descifrados. ``{}`` si no hay."""
        ...

    @abstractmethod
    def set(
        self,
        namespace: str,
        values: dict[str, Any],
        sensitive: frozenset[str] = frozenset(),
    ) -> None:
        """Mergea ``values`` sobre la config existente del namespace y persiste.

        Los campos en ``sensitive`` se cifran en reposo. Valores ``None`` o
        ``""`` se ignoran (no pisan lo existente). El cambio es efectivo
        inmediatamente para todos los lectores del store (sin reiniciar).
        """
        ...

    @abstractmethod
    def masked(self, namespace: str) -> dict[str, Any]:
        """Config del namespace con los campos sensibles enmascarados (``***``)."""
        ...
