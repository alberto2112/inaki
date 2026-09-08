"""Bloques ``tools`` y ``semantic_routing``: tools visibles y routing semántico.

Sección del schema de configuración. Solo declaraciones: sin I/O ni carga de YAML.
Importá desde ``inaki.config.schema`` (o ``inaki.config``).
"""

from __future__ import annotations

from pydantic import Field
from inaki.config.schema._base import _ConfigBaseModel


class ToolsConfig(_ConfigBaseModel):
    """Qué herramientas ve el LLM en cada turno y hasta dónde puede encadenarlas.

    Dos responsabilidades en un mismo bloque:

    - **Selección** (``semantic_routing_*``, ``sticky_ttl``, ``pinned``,
      ``allowed``): qué schemas se le ofrecen al modelo. Mecánica idéntica a la
      de ``skills``, pero acá el costo no es solo de tokens: ofrecerle ~25
      schemas de golpe DEGRADA la elección del modelo, que es la razón de fondo
      del routing.
    - **Ejecución** (``tool_call_max_iterations``, ``circuit_breaker_threshold``):
      las dos barandas del tool loop, para que un turno no se vaya en llamadas
      encadenadas ni se quede pegado reintentando una tool rota.

    Bloque per-agente. Las políticas comunes con ``skills`` viven en
    ``semantic_routing``.
    """

    semantic_routing_min_tools: int = 10
    """Nº de tools a partir del cual se ACTIVA el routing. Con menos o igual, entran todas.

    Umbral de activación, no mínimo de resultados: con
    ``len(tools) <= min_tools`` el LLM ve el registry completo y no se calcula
    embedding. Cuenta los schemas REGISTRADOS en el agente, no los builtins del
    sistema."""

    semantic_routing_top_k: int = 5
    """Máximo de tools que el retrieval devuelve por turno.

    Default 5 (mayor que el de skills): un schema pesa menos que un bloque de
    instrucciones, y el modelo suele necesitar varias tools para una misma
    tarea. Las vivas por ``sticky_ttl`` y las de ``pinned`` se SUMAN a estas, así
    que el set visible del turno puede superar ``top_k``."""

    semantic_routing_min_score: float = 0.0
    """Similitud coseno mínima para que una tool entre en la selección. ``0.0`` = sin piso.

    Con el default manda solo ``top_k``. Subirlo evita ofrecerle al modelo tools
    que no vienen al caso en turnos de charla."""

    tool_call_max_iterations: int = 5
    """Vueltas máximas del tool loop en un turno antes de cortar con error.

    Cada iteración es un ``llm.complete()`` más el batch de tools que pida: es el
    techo de cuánto puede encadenar el modelo para resolver un pedido. Al
    agotarse se levanta ``ToolLoopMaxIterationsError``, conservando el último
    texto del LLM. Subirlo habilita tareas más largas a costa de latencia y
    tokens. No es un techo absoluto de tiempo: un drain de mensajes in-flight
    puede resetear el contador, pero solo hasta 3 veces por turno — justamente
    para que el turno termine."""

    circuit_breaker_threshold: int = 2
    """Fallos NO-retryable de una misma tool, en el mismo turno, antes de bloquearla.

    Al alcanzarlo, las siguientes llamadas a esa tool no se ejecutan: el loop
    devuelve un resultado ``CIRCUIT OPEN`` que le dice al modelo que deje de
    insistir y responda con lo que tiene. Un fallo marcado como retryable NO
    cuenta, y una ejecución exitosa REINICIA el contador. El estado es por turno:
    el turno siguiente arranca con el circuito cerrado."""

    sticky_ttl: int = 3
    """Turnos que una tool seleccionada sigue visible sin volver a ser elegida. ``0`` = desactivado.

    Al ser reseleccionada vuelve al valor completo; si no, decrementa y a cero se
    cae. Evita que una tool desaparezca del set justo cuando el usuario da el
    siguiente paso de la misma tarea con una frase corta. No aplica a las tools
    de ``pinned``, que están siempre visibles y no consumen TTL."""
    pinned: list[str] = Field(default_factory=lambda: ["delegate"])
    """Tools SIEMPRE visibles para el LLM, por fuera del semantic routing.

    Los schemas de estos nombres se unionan al resultado del routing en cada
    turno sin contar contra ``semantic_routing_top_k``. Es para tools de
    ORQUESTACIÓN que el LLM selecciona por razonamiento (``delegate``): una tool
    así debe estar VISIBLE para poder ser razonada — el routing por embedding
    solo la traería si las palabras del USUARIO la matchean (caso real: el LLM
    quería delegar y alucinó un binario porque `delegate` no estaba en su set).
    Nombres inexistentes en el registry se ignoran. No aplica cuando el caller
    fuerza ``tools_override`` (triggers del scheduler) ni en el flujo one-shot
    de delegación (sin routing). Lista vacía = sin pinning."""

    allowed: list[str] | None = None
    """Allow-list de nombres de tools. ``None`` (default) = sin restricción.

    Solo tiene efecto en el flujo ``delegate`` (sub-agente efímero one-shot): el sub
    declara este campo en su YAML para **restringir** qué tools del CALLER puede usar el
    hijo. El builder efímero lo pasa a ``OneShotSettings.allowed_tools`` y
    ``RunAgentOneShotUseCase`` filtra el schema por estos nombres. El filtro corre sobre
    el registry del caller, así que un nombre inexistente se ignora — nunca AMPLÍA sobre el
    padre. En el turno normal (``RunAgentUseCase`` con semantic routing) el campo es inerte."""


class SemanticRoutingConfig(_ConfigBaseModel):
    """Políticas transversales al pipeline de semantic routing (skills + tools).

    Lo que se configura acá vale para AMBOS pipelines a la vez. Los parámetros
    propios de cada uno (umbrales, ``top_k``, TTL) viven en ``skills`` y ``tools``.
    """

    min_words_threshold: int = 0
    """Palabras por debajo de las cuales un turno hereda la selección previa sin re-rutear.
    ``0`` = desactivado.

    Pensado para los "dale", "sí", "y eso?": si el input tiene MENOS palabras que
    este umbral Y hay selección sticky previa (de skills o de tools), el turno
    saltea el cálculo del embedding y reusa la selección anterior intacta — no
    decrementa TTL ni persiste estado. Sin sticky previo (primer turno, o TTL ya
    expirado) el routing corre igual, porque no hay nada que heredar. Con ``0``
    el routing corre siempre."""
