from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel


class ToolResult(BaseModel):
    tool_name: str
    output: str
    success: bool
    error: str | None = None
    retryable: bool = True


class ITool(ABC):
    name: str
    description: str
    parameters_schema: dict  # JSON Schema

    # Disparadores adicionales SOLO para el embedding del semantic routing.
    # NO se muestran al LLM (no van al schema): el LLM recibe únicamente
    # `description`. Su único propósito es enriquecer el texto que se embebe
    # para el matching coseno, mejorando el retrieval cross-lingual.
    #
    # Patrón recomendado: `description` en inglés (comprensión óptima del LLM)
    # y `routing_keywords` con disparadores MULTILINGÜES (es/en/fr) que reflejen
    # cómo un humano expresa la intención de usar la tool. multilingual-e5-small
    # matchea mucho mejor query↔texto dentro del mismo idioma que cruzando idiomas.
    #
    # Default "" → tools que no lo definan se comportan como antes (solo
    # `description` se embebe). 100% backward-compat.
    routing_keywords: str = ""

    # Tool Config Protocol (ver inaki/kernel/ports/tool_config_port.py).
    # Namespace del bloque tool_config.{namespace} en config/tool_config.yaml.
    # Si una tool lo declara (no-vacío), el container la instancia con el
    # kwarg `config_store` (IToolConfigStore) — incluidas las tools de ext/.
    # Default "" → la tool no usa el protocolo y se instancia sin args.
    config_namespace: str = ""

    # Tool privilegiada: solo se ejecuta cuando la pide el operador. La tool
    # DECLARA, el canal DECIDE (`es_operador` por `execute()`) y el kernel HACE
    # CUMPLIR (`run_tool_loop`, justo antes de ejecutar). NUNCA una tool decide
    # su propio privilegio leyendo `ChannelContext` o un ContextVar: la delegación
    # sync corre dentro del turno del padre y lo heredaría.
    # Default False → el resto de las tools no cambia.
    requires_operator: bool = False

    # Privilegio POR OPERACIÓN: operaciones que corren sin operador aunque la tool sea
    # `requires_operator`. Vacía + `requires_operator=True` = la tool entera protegida
    # (el comportamiento histórico). Una tool que la declara TIENE que despachar con
    # `self.operation_of(kwargs)`: despacho y autorización salen de la MISMA función, así
    # no pueden divergir. `ToolRegistry.register` valida la declaración (no vacía exige
    # `requires_operator`, `operation_param` y que cada operación esté en el `enum` del
    # parámetro). Puede fijarse por instancia en `__init__` (una tool que solo expone
    # lectura cuando le faltan las dependencias de escritura).
    unlocked_operations: frozenset[str] = frozenset()

    # Nombre del parámetro que despacha la operación (casi siempre "operation").
    operation_param: str | None = None

    def operation_of(self, kwargs: Mapping[str, Any]) -> str | None:
        """La operación que piden estos kwargs, normalizada; ``None`` si no hay.

        ``None`` (parámetro ausente, vacío o tool sin ``operation_param``) NUNCA está
        desbloqueado: falla cerrado.
        """
        if self.operation_param is None:
            return None
        return str(kwargs.get(self.operation_param) or "").strip().lower() or None

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult: ...


class IToolExecutor(ABC):
    @abstractmethod
    def register(self, tool: ITool) -> None: ...

    @abstractmethod
    async def execute(self, tool_name: str, **kwargs) -> ToolResult: ...

    @abstractmethod
    def requires_operator(self, tool_name: str, kwargs: Mapping[str, Any]) -> bool:
        """True si ESTA llamada es privilegiada (solo corre a pedido del operador).

        Es privilegiada si la tool es ``requires_operator`` y su operación (según
        ``kwargs``) no está en ``unlocked_operations``. Una operación ausente cuenta
        como protegida.

        ABSTRACTO a propósito: un default ``False`` haría que un executor que no
        lo implemente ejecute privilegiadas sin chequeo (falla abierto). Una tool
        inexistente devuelve ``False``: el rechazo de "no encontrada" ya la corta.
        """
        ...

    def operation_of(self, tool_name: str, kwargs: Mapping[str, Any]) -> str | None:
        """La operación que piden estos kwargs, o ``None``. SOLO para redactar el rechazo.

        No autoriza nada (eso es ``requires_operator``, abstracto), por eso tiene default.
        """
        return None

    @abstractmethod
    def get_schemas(self) -> list[dict]:
        """Retorna los schemas de todas las tools registradas para el LLM."""
        ...

    @abstractmethod
    async def get_schemas_relevant(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        min_score: float = 0.0,
    ) -> list[dict]:
        """Retorna los schemas de las tools más relevantes para el query via cosine similarity."""
        ...

    @abstractmethod
    async def get_schemas_relevant_with_scores(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        min_score: float = 0.0,
    ) -> list[tuple[dict, float]]:
        """Schemas seleccionados por RAG con score de similitud coseno (orden descendente por score)."""
        ...
