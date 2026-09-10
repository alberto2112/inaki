"""Bloque ``memories``: memoria a largo plazo, consolidación y reconciliación.

Sección del schema de configuración. Solo declaraciones: sin I/O ni carga de YAML.
Importá desde ``inaki.config.schema`` (o ``inaki.config``).
"""

from __future__ import annotations

from pydantic import ConfigDict

from inaki.config.schema._base import RuntimePath, _ConfigBaseModel
from inaki.config.schema.llm import LLMConfig

# Los DTOs ``Resolved*Config`` (feature + creds compuestas) viven en su módulo —
# cada familia los declara en su ``base.py`` (``inaki/llm``, ``inaki/embedding``,
# transcripción en ``inaki/perception``). El ``wiring.py`` de cada módulo los
# compone desde acá.


class MemoryLLMConfig(_ConfigBaseModel):
    """
    Override parcial de ``LLMConfig`` para el LLM base COMPARTIDO por los dos
    jobs de memoria (consolidación y reconciliación) en modo directo.

    Todos los campos son opcionales. Solo los campos EXPLÍCITAMENTE presentes
    en el YAML pisan al ``llm.*`` del agente; los ausentes se heredan.

    Semántica ``null`` vs ausente (relevante para distinguir override de herencia):
      - Clave ausente en YAML → no está en ``model_fields_set`` → hereda del base.
      - Clave presente con valor ``null`` → está en ``model_fields_set`` con valor
        ``None`` → pisa al base con ``None`` (útil para, p. ej., apagar
        ``reasoning_effort`` en los jobs de memoria sin tocar el LLM del agente).

    Las credenciales NO viven acá — si el override cambia ``provider``, las creds
    se resuelven automáticamente desde el registry ``providers`` del nivel
    superior. Ver ``inaki.memory.wiring.resolver_llm_de_memorias``.

    NOTA: ``agent_id`` ya NO vive acá. La delegación a sub-agente es POR JOB y
    se declara en ``consolidation.agent_id`` / ``reconciliation.agent_id`` —
    cada job tiene su propio sub-agente especializado (extractor vs reconciler),
    con prompts distintos. El sub-agente, vía el merge de 4 capas, sobreescribe
    esta config LLM base de forma individual en su propio fichero.
    """

    provider: str | None = None
    """Override de ``llm.provider`` para los jobs de memoria. Ausente → hereda del agente.

    Cambiarlo basta para mover los jobs a otro vendor: las credenciales se
    resuelven solas desde ``providers.{provider}``, no van acá."""

    model: str | None = None
    """Override de ``llm.model`` para los jobs de memoria. Ausente → hereda del agente.

    El caso típico del bloque: chatear con un modelo caro y extraer/reconciliar
    recuerdos con uno barato."""

    temperature: float | None = None
    """Override de ``llm.temperature`` para los jobs de memoria. Ausente → hereda del agente.

    La extracción devuelve JSON estructurado, así que suele convenir bajarla
    respecto de la del chat."""

    max_tokens: int | None = None
    """Override de ``llm.max_tokens`` para los jobs de memoria. Ausente → hereda del agente."""

    reasoning_effort: str | None = None
    """Override de ``llm.reasoning_effort`` para los jobs de memoria. Ausente → hereda del agente.

    Es el campo donde más se nota el tri-estado: escribir ``reasoning_effort: null``
    APAGA el thinking en los jobs de memoria sin tocar el LLM conversacional,
    mientras que omitir la clave hereda el del agente."""

    timeout_seconds: int | None = None
    """Override de ``llm.timeout_seconds`` (segundos) para los jobs de memoria.
    Ausente → hereda del agente."""


class ConsolidationConfig(_ConfigBaseModel):
    """Configuración del job de consolidación (extracción → digest → trim)."""

    enabled: bool = True
    """Habilita la consolidación para ESTE agente. Flag PER-AGENT (agents/{id}.yaml)."""

    schedule: str = "0 3 * * *"
    """Cron de la consolidación global nocturna (una tarea que itera todos los agentes)."""

    delay_seconds: int = 2
    """
    Pausa (segundos) entre llamadas al LLM extractor. Aplica TANTO entre agentes
    como entre scopes ``(channel, chat_id)`` del mismo agente. Evita rate-limits.
    """

    keep_last_messages: int = 0
    """Mensajes CONVERSACIONALES a preservar por scope tras consolidar.

    0 = fallback del sistema (84). Cuenta ``user`` + ``assistant`` de texto: el
    rastro protocolar (``tool`` results y el ``assistant`` con ``tool_calls``) NO
    consume presupuesto — antes sí, y un turno con herramientas costaba como
    varios turnos de conversación. Ver ``trim-cuenta-conversacion`` en
    ``docs/migraciones.md``."""

    min_relevance_score: float = 0.5
    """Umbral mínimo (0.0-1.0) para persistir un recuerdo extraído por el LLM."""

    channels_infused: list[str] | None = None
    """
    Canales cuyo historial se incluye en la consolidación.

    ``None`` o lista vacía → se procesan mensajes de todos los canales.
    Si se especifica, solo se consolidan mensajes donde ``channel`` está en la lista.
    Ejemplo: ``["telegram"]`` — no consolida mensajes de CLI ni daemon.
    """

    agent_id: str | None = None
    """
    Sub-agente EXTRACTOR opcional (debe existir en ``agents/sub-agents/``).

    Cuando se especifica, la extracción delega a ese sub-agente vía
    ``RunAgentOneShotUseCase`` en lugar del prompt hardcodeado. El
    ``system_prompt`` del sub-agente se usa como prompt extractor (debe devolver
    JSON con la lista de recuerdos) y el sub-agente usa su propia config LLM.
    Si el ``agent_id`` no resuelve a un sub-agente válido, el arranque loggea un
    ERROR y la consolidación cae de vuelta al prompt extractor por defecto.
    """


class ReconciliationConfig(_ConfigBaseModel):
    """Configuración del job de reconciliación de memoria («reflection»)."""

    enabled: bool = False
    """
    Habilita el job de reconciliación para ESTE agente. Flag PER-AGENT.

    Opt-in (default ``False``) por ser una operación más costosa que la
    consolidación ordinaria (una llamada LLM por cluster de recuerdos similares).
    Es INDEPENDIENTE de ``consolidation.enabled`` — se puede correr reconciliación
    sobre recuerdos preexistentes aunque la consolidación esté apagada.
    """

    schedule: str = "0 4 * * 1"
    """Cron de la tarea builtin por agente. Evaluado en tz del usuario. Default: lunes 04:00."""

    similarity_threshold: float = 0.80
    """
    Umbral de similitud coseno (0.0-1.0) para agrupar dos recuerdos en un cluster.
    Default ``0.80`` (conservador — solo recuerdos muy similares se agrupan).
    """

    top_k: int = 10
    """
    Vecinos máximos por seed al armar un cluster. Un valor generoso compensa que
    ``search_with_scores`` no filtra por scope nativamente (limitación V1).
    """

    agent_id: str | None = None
    """
    Sub-agente RECONCILIADOR opcional (debe existir en ``agents/sub-agents/``).

    Cuando se especifica, la reconciliación delega a ese sub-agente vía one-shot
    en lugar del prompt hardcodeado; el sub-agente usa su propia config LLM. Si no
    resuelve a un sub-agente válido, el arranque loggea ERROR y cae al prompt por
    defecto + LLM compartido (graceful).
    """


class MemoriesConfig(_ConfigBaseModel):
    """
    Configuración del subsistema de memoria a largo plazo.

    Estructura:
      - Campos de nivel raíz: store + digest COMPARTIDOS por ambos jobs.
      - ``llm``: LLM base COMPARTIDO (provider/model/...) para los dos jobs en modo
        directo. Sin ``agent_id`` — la delegación a sub-agente es por job.
      - ``consolidation`` / ``reconciliation``: secciones hermanas, cada una con su
        ``enabled``, ``schedule``, parámetros propios y ``agent_id`` de sub-agente.
    """

    model_config = ConfigDict(validate_default=True)  # RuntimePath en los defaults

    db_filename: RuntimePath = "data/inaki.db"
    """Fichero SQLite del store de recuerdos (tablas ``memories`` y ``memory_embeddings``).

    Relativo al home de instancia; se reancla con ``--home`` / ``INAKI_HOME``. Un
    path absoluto se usa tal cual. Los agentes que comparten fichero se aíslan
    por columna ``agent_id``; para aislamiento FÍSICO, dale a cada agente un
    ``db_filename`` distinto."""

    digest_filename: RuntimePath = "mem/digest_{channel}_{chat_id}.md"
    """Template del fichero markdown del digest, con los placeholders ``{channel}`` y ``{chat_id}``.

    El digest es el resumen de recuerdos que ``RunAgentUseCase`` inyecta en el
    prompt y que la consolidación regenera. Se aísla por scope: los placeholders
    se sustituyen sanitizados (todo lo que no sea ``[a-zA-Z0-9_-]`` pasa a ``_``,
    vacío a ``default``), así que los recuerdos de un grupo no se filtran a otro.
    Un template SIN placeholders devuelve la misma ruta para todos los scopes.
    Relativo al home de instancia; se reancla con ``--home`` / ``INAKI_HOME``."""

    digest_size: int = 14
    """Nº de recuerdos más recientes volcados al digest markdown. Orden: created_at DESC."""

    llm: MemoryLLMConfig | None = None
    """
    LLM base COMPARTIDO por consolidación y reconciliación (modo directo).
    ``None`` → ambos jobs reusan el LLM del agente. La delegación a sub-agente
    (por job) se declara en ``consolidation.agent_id`` / ``reconciliation.agent_id``.
    """

    consolidation: ConsolidationConfig = ConsolidationConfig()
    """Job nocturno que extrae recuerdos del historial, regenera el digest y lo recorta.

    Trae su propio ``enabled`` (per-agente), su cron y su ``agent_id`` de
    sub-agente extractor. Es INDEPENDIENTE de ``reconciliation``."""

    reconciliation: ReconciliationConfig = ReconciliationConfig()
    """Job de «reflection» que agrupa recuerdos similares y resuelve los contradictorios.

    Trae su propio ``enabled`` (per-agente, opt-in), su cron y su ``agent_id`` de
    sub-agente reconciliador. Corre aunque la consolidación esté apagada."""

    # La resolución del digest path y de keep_last_messages (lógica de dominio
    # que solo core consume) vive en inaki/kernel/domain/agent_settings.py
    # (``MemorySettings``). ``inaki/memory/wiring.py::build_memory_settings`` traduce este modelo a ese VO.

    def merged_llm_config(self, base: LLMConfig) -> LLMConfig:
        """
        Devuelve la ``LLMConfig`` efectiva (sin creds) tras aplicar el override
        compartido ``memories.llm``.

        Merge field-by-field: los campos que el usuario seteó EXPLÍCITAMENTE
        en ``memories.llm.*`` (incluso ``null``) pisan al ``base``; el resto hereda.
        Si no hay override, devuelve el ``base`` tal cual.

        ÚNICA excepción declarada al motor de merge del dominio
        (``inaki/config/merge.py``), y a propósito: el motor opera sobre dicts
        CRUDOS antes de validar, y este merge ocurre DESPUÉS, entre dos modelos ya
        validados. La semántica es la misma —``model_fields_set`` es el
        equivalente pydantic de "la clave está escrita en el YAML", así que
        ausente hereda y ``null`` explícito pisa— pero expresada en el mundo de
        los modelos. Absorberlo obligaría a mergear ``llm`` y ``memories.llm`` en
        crudo antes de validar, lo que convertiría ``MemoriesConfig.llm`` en un
        ``LLMConfig`` y rompería el tri-estado (ausente hereda, ``null`` pisa, valor
        escribe) con el que se edita ``memories.llm.*`` por capa. No vale el cambio: cualquier ajuste a la semántica de
        merge se hace en el motor y se replica acá.

        Las credenciales se resuelven aparte contra el registry ``providers``
        — la composición del ``ResolvedLLMConfig`` (DTO de adapters) vive en
        ``inaki.memory.wiring.resolver_llm_de_memorias``.
        """
        if self.llm is None:
            return base

        fields_set = self.llm.model_fields_set
        overrides = {f: getattr(self.llm, f) for f in fields_set}
        return base.model_copy(update=overrides)
