"""Schema de configuración de Inaki — modelos Pydantic y helpers de path.

Solo declaraciones: sin I/O, sin carga de YAML. El loader (``config_loader``)
y la fachada (``config``) viven aparte. Importá desde ``infrastructure.config``
(fachada) salvo que necesites explícitamente solo el schema.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

from infrastructure.home import get_inaki_home

logger = logging.getLogger(__name__)


def _expand_user_str(v: Any) -> Any:
    """Expand `~` in a string path. Non-strings pass through untouched."""
    if isinstance(v, str):
        return str(Path(v).expanduser())
    return v


def _expand_user_list(v: Any) -> Any:
    """Expand `~` in every string element of a list. Non-lists pass through."""
    if isinstance(v, list):
        return [str(Path(x).expanduser()) if isinstance(x, str) else x for x in v]
    return v


ExpandedPath = Annotated[str, BeforeValidator(_expand_user_str)]
ExpandedPathList = Annotated[list[str], BeforeValidator(_expand_user_list)]


# Valores SQLite especiales que NO deben interpretarse como paths.
_SQLITE_SPECIAL = {":memory:"}


def _resolve_runtime_path(v: Any) -> Any:
    """
    Resuelve un path de runtime contra el home de instancia (`get_inaki_home()`).

    - Valores no-str pasan sin tocar (ya vienen normalizados).
    - Valores especiales de SQLite (`:memory:`) pasan tal cual.
    - Paths absolutos (incluyendo `~/...` tras expansión) se usan tal cual.
    - Paths relativos se anclan bajo el home de instancia (`get_inaki_home()`).
    """
    if not isinstance(v, str):
        return v
    if v in _SQLITE_SPECIAL:
        return v
    p = Path(v).expanduser()
    if p.is_absolute():
        return str(p)
    return str(get_inaki_home() / p)


RuntimePath = Annotated[str, BeforeValidator(_resolve_runtime_path)]


# ---------------------------------------------------------------------------
# Sub-configs
# ---------------------------------------------------------------------------


class AppConfig(BaseModel):
    name: str = "Inaki"
    log_level: str = "INFO"
    ext_dirs: ExpandedPathList = ["ext", "~/.inaki/ext"]
    default_agent: str = "general"


class ProviderConfig(BaseModel):
    """
    Entrada del registry top-level de proveedores.

    Cada entrada representa UN vendor (groq, openai, openrouter, ollama, etc.)
    con sus credenciales y endpoint. Las features (`llm`, `embedding`,
    `transcription`, `memories.llm`) referencian entradas por nombre vía su
    campo ``provider: <key>``, eliminando duplicación de ``api_key``/``base_url``.

    Campos:
      - ``type``: nombre del adapter. Si es ``None``, se resuelve a la key del
        dict (``providers.groq`` → ``type == "groq"``). Solo se explicita cuando
        se quieren múltiples entradas del mismo adapter con creds distintas
        (p. ej. ``providers.groq-work: {type: groq, api_key: K2}``).
      - ``api_key``: credencial. Opcional para providers locales que no la
        requieren (ollama, e5_onnx).
      - ``base_url``: override del default del adapter. Opcional.

    ``extra="forbid"`` atrapa typos temprano (``api_ky``).
    """

    model_config = ConfigDict(extra="forbid")

    type: str | None = None
    api_key: str | None = None
    base_url: str | None = None


_LLM_TIMEOUT_FALLBACK = 60


class LLMConfig(BaseModel):
    provider: str = "openrouter"
    model: str = "anthropic/claude-3-5-haiku"
    temperature: float = 0.7
    max_tokens: int = 2048
    reasoning_effort: str | None = None
    timeout_seconds: int = _LLM_TIMEOUT_FALLBACK
    """Timeout HTTP del request al provider, en segundos.

    Default ``60``. Recomendado subirlo (180-300) cuando se usa thinking mode
    sobre queries complejas, donde el modelo puede tardar mucho más en
    responder. Valores no-int, ``<= 0`` o no parseables se sanitizan al
    fallback de 60s para no fallar el bootstrap por config mal definida.
    """

    request_delay_seconds: float = 2.0
    """Espera mínima (segundos) ANTES de cada llamada al provider dentro del
    loop agéntico, EXCEPTO la primera del turno.

    Default ``2.0``. Evita saturar el rate limiter del provider cuando el modelo
    encadena varias tool calls en un mismo turno (cada iteración del loop es un
    ``llm.complete()``): sin throttle, 5 tool calls disparan 5 requests
    back-to-back. La primera llamada del turno NO se demora (sería latencia pura
    sin proteger nada — el rate limiter se satura por las llamadas encadenadas).
    ``0`` desactiva el throttle. Valores negativos se clampean a ``0``; valores
    no parseables caen al default ``2.0`` para no fallar el bootstrap.
    """

    @field_validator("timeout_seconds", mode="before")
    @classmethod
    def _coerce_timeout(cls, v: object) -> int:
        try:
            n = int(v)  # type: ignore[call-overload]
            return n if n > 0 else _LLM_TIMEOUT_FALLBACK
        except (TypeError, ValueError):
            return _LLM_TIMEOUT_FALLBACK

    @field_validator("request_delay_seconds", mode="before")
    @classmethod
    def _coerce_request_delay(cls, v: object) -> float:
        try:
            n = float(v)  # type: ignore[arg-type]
            return n if n >= 0 else 0.0
        except (TypeError, ValueError):
            return 2.0


class EmbeddingConfig(BaseModel):
    model_config = ConfigDict(validate_default=True)  # RuntimePath en los defaults

    provider: str = "e5_onnx"
    model_dirname: RuntimePath = "models/e5-small"  # solo e5_onnx — relativo a ~/.inaki/
    model: str = "text-embedding-3-small"  # solo openai
    dimension: int = 384
    cache_filename: RuntimePath = "data/embedding_cache.db"  # relativo a ~/.inaki/


class TranscriptionConfig(BaseModel):
    """Config del provider de transcripción de audio (opcional)."""

    provider: str = "groq"
    model: str = "whisper-large-v3-turbo"
    language: str | None = None  # None → auto-detect por el modelo
    timeout_seconds: int = 60  # segundos para el request HTTP
    max_audio_mb: int = 25  # límite de tamaño de audio (MB) — Groq Whisper: 25


# Los DTOs ``Resolved*Config`` (feature + creds compuestas) viven en la capa
# adapters — cada familia los declara en su ``base.py`` (providers, embedding,
# transcription). Las factories de infrastructure los componen desde acá.


class MemoryLLMConfig(BaseModel):
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
    superior. Ver ``AgentContainer._resolve_memories_llm`` (container.py).

    NOTA: ``agent_id`` ya NO vive acá. La delegación a sub-agente es POR JOB y
    se declara en ``consolidation.agent_id`` / ``reconciliation.agent_id`` —
    cada job tiene su propio sub-agente especializado (extractor vs reconciler),
    con prompts distintos. El sub-agente, vía el merge de 4 capas, sobreescribe
    esta config LLM base de forma individual en su propio fichero.
    """

    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    reasoning_effort: str | None = None
    timeout_seconds: int | None = None


class ConsolidationConfig(BaseModel):
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
    """Mensajes a preservar por agente tras consolidar. 0 = fallback del sistema (84)."""

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


class ReconciliationConfig(BaseModel):
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


class MemoriesConfig(BaseModel):
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

    db_filename: RuntimePath = "data/inaki.db"  # relativo a ~/.inaki/
    # Template del digest markdown — admite los placeholders ``{channel}`` y
    # ``{chat_id}``, sustituidos sanitizados en ``resolved_digest_path``. El digest
    # se aísla por (channel, chat_id): consolidación lo regenera, run_agent lo lee.
    digest_filename: RuntimePath = "mem/digest_{channel}_{chat_id}.md"  # relativo a ~/.inaki/
    digest_size: int = 14
    """Nº de recuerdos más recientes volcados al digest markdown. Orden: created_at DESC."""

    llm: MemoryLLMConfig | None = None
    """
    LLM base COMPARTIDO por consolidación y reconciliación (modo directo).
    ``None`` → ambos jobs reusan el LLM del agente. La delegación a sub-agente
    (por job) se declara en ``consolidation.agent_id`` / ``reconciliation.agent_id``.
    """

    consolidation: ConsolidationConfig = ConsolidationConfig()
    reconciliation: ReconciliationConfig = ReconciliationConfig()

    # La resolución del digest path y de keep_last_messages (lógica de dominio
    # que solo core consume) vive en core/domain/value_objects/agent_settings.py
    # (``MemorySettings``). El container traduce este modelo a ese VO.

    def merged_llm_config(self, base: LLMConfig) -> LLMConfig:
        """
        Devuelve la ``LLMConfig`` efectiva (sin creds) tras aplicar el override
        compartido ``memories.llm``.

        Merge field-by-field: los campos que el usuario seteó EXPLÍCITAMENTE
        en ``memories.llm.*`` (incluso ``null``) pisan al ``base``; el resto hereda.
        Si no hay override, devuelve el ``base`` tal cual.

        Las credenciales se resuelven aparte contra el registry ``providers``
        — la composición del ``ResolvedLLMConfig`` (DTO de adapters) vive en
        ``AgentContainer._resolve_memories_llm``.
        """
        if self.llm is None:
            return base

        fields_set = self.llm.model_fields_set
        overrides = {f: getattr(self.llm, f) for f in fields_set}
        return base.model_copy(update=overrides)


class ChatHistoryConfig(BaseModel):
    model_config = ConfigDict(validate_default=True)  # RuntimePath en los defaults

    db_filename: RuntimePath = "data/history.db"  # relativo a ~/.inaki/
    max_messages: int = 0  # 0 = sin límite; N = últimos N mensajes al LLM
    merge_chats: bool = False  # False = aislar historial por (channel, chat_id);
    # True = compartir todo el historial del agente entre canales/chats


class ChannelsGlobalConfig(BaseModel):
    """Flags transversales de presentación al usuario en cualquier canal.

    Se configura SOLO a nivel global (``global.yaml`` → ``channels:``). No hay
    override per-agent: ``AgentConfig.channels`` (dict de adapters telegram/cli/…)
    es una estructura distinta y mantiene su rol. Si el usuario pone estos
    flags en ``agents/{id}.yaml`` por error, el merge los filtra en
    ``load_agent_config`` para no contaminar el dict de adapters.
    """

    thinking_indicator: bool = False
    """Mostrar "Thinking..." en el canal cuando el modelo está razonando.

    Solo aplica si el provider activa thinking mode (``reasoning_effort``).
    ``False`` (default) → el bot permanece silencioso durante el razonamiento.
    """


class ChannelFallbackConfig(BaseModel):
    """Config de fallbacks para el routing de canales del scheduler.

    Cuando una task dispara un envío a un canal que no tiene sink nativo
    (p. ej. ``cli``, ``rest``, ``daemon``), el ``ChannelRouter`` resuelve
    el destino efectivo aplicando esta cascada:

      1. Sink nativo registrado para el prefix del target.
      2. Entry en ``overrides`` para el ``channel_type`` del target.
      3. ``default`` global (si está configurado).
      4. Fallback hardcoded: ``file://~/.inaki/data/scheduler-fallback.log``.

    Atributos:
        default: Target string (p. ej. ``"file:///var/log/x.log"``,
            ``"telegram:12345"``, ``"null:"``) usado cuando no hay override
            específico. ``None`` delega al fallback hardcoded.
        overrides: Mapa ``channel_type → target string`` para redirigir
            canales concretos. Ejemplo: ``{"cli": "telegram:123"}`` envía
            los mensajes que nacieron desde CLI hacia ese chat de Telegram.
    """

    default: str | None = None
    overrides: dict[str, str] = {}


class SchedulerConfig(BaseModel):
    model_config = ConfigDict(validate_default=True)  # RuntimePath en los defaults

    enabled: bool = True
    db_filename: RuntimePath = "data/scheduler.db"  # relativo a ~/.inaki/
    fallback_log_filename: RuntimePath = "data/scheduler-fallback.log"
    """Fallback de último recurso del router de dispatch (cascada). Relativo al home de
    instancia; se reancla con ``--home`` / ``INAKI_HOME``. El composition root lo envuelve
    en ``file://`` y lo inyecta al ``ChannelRouter`` (por privacidad, bajo ``<home>/data/``)."""
    max_retries: int = 3
    retry_backoff_seconds: float = 10.0  # espera lineal entre reintentos (1x, 2x, 3x...)
    max_tasks_per_agent: int = 20  # tareas activas (pending/running) por agente
    output_truncation_size: int = 65536
    channel_fallback: ChannelFallbackConfig = ChannelFallbackConfig()


class SkillsConfig(BaseModel):
    semantic_routing_min_skills: int = 10
    semantic_routing_top_k: int = 3
    semantic_routing_min_score: float = 0.0
    sticky_ttl: int = 3  # Turnos que una skill seleccionada sobrevive; 0 = disabled


class ToolsConfig(BaseModel):
    semantic_routing_min_tools: int = 10
    semantic_routing_top_k: int = 5
    semantic_routing_min_score: float = 0.0
    tool_call_max_iterations: int = 5
    circuit_breaker_threshold: int = 2
    sticky_ttl: int = 3  # Turnos que una tool seleccionada sobrevive; 0 = disabled
    allowed: list[str] | None = None
    """Allow-list de nombres de tools. ``None`` (default) = sin restricción.

    Solo tiene efecto en el flujo ``delegate`` (sub-agente efímero one-shot): el sub
    declara este campo en su YAML para **restringir** qué tools del CALLER puede usar el
    hijo. El builder efímero lo pasa a ``OneShotSettings.allowed_tools`` y
    ``RunAgentOneShotUseCase`` filtra el schema por estos nombres. El filtro corre sobre
    el registry del caller, así que un nombre inexistente se ignora — nunca AMPLÍA sobre el
    padre. En el turno normal (``RunAgentUseCase`` con semantic routing) el campo es inerte."""


class SemanticRoutingConfig(BaseModel):
    """Políticas transversales al pipeline de semantic routing (skills + tools).

    ``min_words_threshold``: si el user_input tiene MENOS palabras que este
    umbral Y existe una selección sticky previa (skills o tools), el turno
    saltea el cálculo del embedding y hereda la selección del turno anterior
    intacta (no decrementa TTL, no persiste estado). ``0`` desactiva la
    feature y mantiene el comportamiento histórico (routing corre siempre).
    """

    min_words_threshold: int = 0


ContainmentMode = Literal["strict", "warn", "off"]


class WorkspaceConfig(BaseModel):
    """
    Workspace sobre el que operan las tools de filesystem.

    `path` — directorio raíz donde se resuelven los paths relativos.
    `containment` — guard de contención para paths absolutos y escapes via `..`:
      - "strict"  → bloquea cualquier path fuera del workspace (recomendado en prod)
      - "warn"    → loggea warning pero permite el acceso
      - "off"     → sin check (útil en desarrollo)
    """

    path: ExpandedPath = "~/inaki-workspace"
    containment: ContainmentMode = "strict"

    def model_post_init(self, __context: object) -> None:
        # Expand ~ in the default value (BeforeValidator no corre en defaults de clase).
        object.__setattr__(self, "path", str(Path(self.path).expanduser()))


class RemoteBroadcastConfig(BaseModel):
    """Config de conexión al servidor broadcast remoto (modo client)."""

    host: str
    """Dirección del servidor en formato ``ip:port`` (ej: ``"192.168.1.10:9000"``)."""

    auth: str
    """Secreto compartido con el servidor para autenticación HMAC-SHA256."""


class BroadcastEmitConfig(BaseModel):
    """Flags por agente que controlan qué tipos de eventos se emiten al broadcast.

    Cada flag corresponde a un ``event_type`` del ``BroadcastMessage``:

    - ``assistant_response`` (default ``True``): respuestas del LLM tras un turno.
      Backward-compat con el comportamiento original del broadcast.
    - ``user_input_voice`` (default ``False``): transcripciones de audio. El admin
      lo activa en UN bot del grupo con capacidad de transcripción para evitar
      duplicados.
    - ``user_input_photo`` (default ``False``): descripciones de foto. El admin
      lo activa en UN bot del grupo con capacidad de visión.

    El modelo es ``strict=True`` para rechazar coerciones implícitas (e.g.,
    string ``"yes"`` o entero ``2`` no-booleano).
    """

    model_config = {"strict": True}

    assistant_response: bool = True
    """Si ``True``, emite ``event_type="assistant_response"`` tras cada turno LLM en grupos."""

    user_input_voice: bool = False
    """Si ``True``, emite ``event_type="user_input_voice"`` tras transcribir un audio."""

    user_input_photo: bool = False
    """Si ``True``, emite ``event_type="user_input_photo"`` tras procesar una foto."""


class BroadcastConfig(BaseModel):
    """
    Config del **transporte** de broadcast TCP entre instancias de Inaki.

    Esta clase modela SOLO la capa de red (topología + emisión de eventos). El
    **comportamiento del bot en grupos** (``behavior``, ``bot_username``,
    ``rate_limiter``, ``rate_limiter_window``) NO vive acá: vive en
    ``TelegramGroupsConfig`` (``channels.telegram.groups``), porque aplica a
    cualquier grupo — haya o no broadcast TCP activo. Mezclar ambos forzaba a
    levantar el transporte solo para configurar cómo responde el bot.

    Un nodo opera como **servidor** si declara ``port`` (sin ``remote``).
    Un nodo opera como **cliente** si declara ``remote`` (sin ``port``).
    Ambos ausentes → broadcast inactivo para ese canal.

    Validaciones:
    - ``port`` y ``remote`` son mutuamente excluyentes (``port XOR remote``).
    - Si ``port`` está seteado → ``auth`` es obligatorio.
    - ``port`` debe estar en el rango 1024..65535.
    """

    port: int | None = None
    """Puerto TCP en el que escucha el servidor. ``None`` → modo cliente."""

    remote: RemoteBroadcastConfig | None = None
    """Config del servidor remoto al que conectar como cliente. ``None`` → modo servidor."""

    auth: str | None = None
    """Secreto HMAC-SHA256 del servidor. Obligatorio cuando ``port`` está seteado."""

    emit: BroadcastEmitConfig = BroadcastEmitConfig()
    """Flags que controlan qué tipos de eventos se emiten al broadcast.
    Sin override usa los defaults: solo ``assistant_response`` activo."""

    @model_validator(mode="after")
    def _validar_topologia(self) -> "BroadcastConfig":
        """Valida que el nodo sea server XOR client, y que server tenga auth."""
        tiene_port = self.port is not None
        tiene_remote = self.remote is not None

        if tiene_port and tiene_remote:
            raise ValueError(
                "BroadcastConfig: 'port' y 'remote' son mutuamente excluyentes — "
                "un nodo no puede ser servidor y cliente simultáneamente."
            )

        if not tiene_port and not tiene_remote:
            raise ValueError(
                "BroadcastConfig: debe definirse 'port' (modo servidor) o "
                "'remote' (modo cliente) — no pueden estar ambos ausentes."
            )

        if tiene_port:
            if self.auth is None:
                raise ValueError(
                    "BroadcastConfig: 'auth' es obligatorio cuando 'port' está definido."
                )
            if not (1024 <= self.port <= 65535):  # type: ignore[operator]
                raise ValueError(
                    f"BroadcastConfig: 'port' debe estar en el rango 1024..65535, "
                    f"recibido: {self.port}."
                )

        return self


class TelegramGroupsConfig(BaseModel):
    """
    Config tipada del comportamiento del bot en chats grupales.

    Cubre dos cosas:
    - **Timing/reacciones** (``min_delay_response``, ``max_delay_response``,
      ``reactions``): opcionales, ``None`` = "heredar del padre" (``reactions``)
      o "usar default del módulo" (delays).
    - **Política de respuesta** (``behavior``, ``bot_username``, ``rate_limiter``,
      ``rate_limiter_window``): cómo decide el bot responder en un grupo. Antes
      vivían en ``BroadcastConfig``, lo que obligaba a levantar el transporte TCP
      solo para configurarlos. Ahora aplican a cualquier grupo, con o sin broadcast.
    """

    model_config = ConfigDict(extra="allow")

    min_delay_response: float | None = None
    """Delay mínimo (segundos) antes de flushar el buffer de grupo al LLM. ``None`` → default del módulo."""

    max_delay_response: float | None = None
    """Delay máximo (segundos) antes de flushar el buffer. ``None`` → default del módulo."""

    reactions: bool | None = None
    """Override del flag ``channels.telegram.reactions`` para chats grupales. ``None`` → hereda del padre."""

    behavior: Literal["listen", "mention", "autonomous"] = "mention"
    """
    Modo de comportamiento en grupos:
    - ``listen`` → nunca invoca el LLM, solo escucha.
    - ``mention`` → invoca el LLM solo si el mensaje menciona al bot (requiere ``bot_username``).
    - ``autonomous`` → invoca el LLM ante cualquier mensaje (sujeto a rate limiter).
    """

    bot_username: str | None = None
    """Username del bot Telegram (sin ``@``) para detección de menciones en modo ``mention``."""

    rate_limiter: int = 5
    """Máximo de respuestas proactivas (modo ``autonomous``) por ventana por chat.

    El primer mensaje que SUPERA este límite (``counter > rate_limiter``) es bloqueado;
    es decir, exactamente ``rate_limiter`` mensajes pasan por ventana."""

    rate_limiter_window: int = 30
    """Duración de la ventana del rate limiter en segundos. Default 30s.

    Importante: el ciclo bot-to-bot toma típicamente 15-40s (delay de flush + LLM + red).
    Si la ventana es menor que el ciclo, el contador se resetea entre intercambios
    y el limiter es inefectivo — bots pueden hablar indefinidamente. Para grupos con
    ``behavior='autonomous'`` se recomienda 300s (5min) o más."""

    @model_validator(mode="after")
    def _validar_delays(self) -> "TelegramGroupsConfig":
        if (
            self.min_delay_response is not None
            and self.max_delay_response is not None
            and self.min_delay_response > self.max_delay_response
        ):
            raise ValueError(
                f"TelegramGroupsConfig: min_delay_response ({self.min_delay_response}) "
                f"no puede ser mayor que max_delay_response ({self.max_delay_response})."
            )
        if self.min_delay_response is not None and self.min_delay_response < 0:
            raise ValueError(
                f"TelegramGroupsConfig: min_delay_response debe ser >= 0, recibido: {self.min_delay_response}."
            )
        if self.max_delay_response is not None and self.max_delay_response < 0:
            raise ValueError(
                f"TelegramGroupsConfig: max_delay_response debe ser >= 0, recibido: {self.max_delay_response}."
            )
        return self


class TelegramChannelConfig(BaseModel):
    """
    Config tipada del canal Telegram.

    Soporta ``extra="allow"`` para no romper campos desconocidos que puedan
    existir en configs de usuario hasta que sean adoptados formalmente.
    """

    model_config = ConfigDict(extra="allow")

    token: str = ""
    """Token del bot de Telegram (BotFather). Requerido para que el canal levante."""

    allowed_user_ids: list[int] = Field(default_factory=list)
    """IDs de usuarios autorizados en CHATS PRIVADOS. Lista vacía = sin restricción.
    NO aplica en grupos (los grupos se controlan solo por ``allowed_chat_ids``)."""

    allowed_chat_ids: list[int] = Field(default_factory=list)
    """IDs de grupos autorizados. Lista vacía = el bot NO responde en grupos (solo
    chats privados). En un grupo autorizado cualquier usuario puede interactuar:
    ``allowed_user_ids`` no se evalúa en grupos."""

    reactions: bool = False
    """Si True, el bot envía una reacción emoji tras procesar un mensaje."""

    voice_enabled: bool = True
    """Si True, el bot acepta mensajes de voz y los transcribe."""

    add_llm_timestamp: bool = False
    """Si True, ``RunAgentUseCase`` antepone ``[YYYY-MM-DD HH:MM:SS TZ] `` al
    ``content`` de cada mensaje USER/ASSISTANT (privados y grupos) antes de
    armar el prompt para el LLM. Default ``False`` para mantener
    compatibilidad. El timestamp sale del ``Message.timestamp`` ya persistido
    en la DB; no se duplica en el ``content`` almacenado."""

    broadcast: BroadcastConfig | None = None
    """Config del canal de broadcast entre instancias. None = broadcast inactivo."""

    groups: TelegramGroupsConfig | None = None
    """Config específica para chats grupales (delays, override de reactions). None = todos los defaults."""


class KnowledgeSourceConfig(BaseModel):
    """Configuración de una fuente de conocimiento externa."""

    id: str
    """Identificador único de la fuente (usado para rutas de DB y CLI)."""

    type: str
    """Tipo de fuente: 'document' | 'sqlite'."""

    enabled: bool = True
    """Si False, la fuente se ignora al construir el KnowledgeOrchestrator."""

    description: str = ""
    """Descripción de la fuente (inyectada en el system prompt)."""

    path: ExpandedPath | None = None
    """Ruta al directorio de documentos (solo para type='document')."""

    glob: str = "**/*.md"
    """Glob pattern para seleccionar archivos (solo para type='document')."""

    chunk_size: int = 500
    """Tamaño de cada chunk en palabras (solo para type='document')."""

    chunk_overlap: int = 80
    """Solapamiento entre chunks en palabras (solo para type='document')."""

    top_k: int = 3
    """Resultados máximos a recuperar de esta fuente por turno."""

    min_score: float = 0.5
    """Score mínimo de coseno para incluir un chunk."""


class KnowledgeConfig(BaseModel):
    """Configuración global del pipeline de knowledge pre-fetch."""

    model_config = ConfigDict(validate_default=True)

    enabled: bool = True
    """Si False, el pre-fetch se saltea completamente en cada turno."""

    db_dirname: RuntimePath = "knowledge"
    """Directorio (relativo al home de instancia) de las DBs de índice por fuente:
    ``<home>/knowledge/{source_id}.db``. Se reancla con ``--home`` / ``INAKI_HOME``."""

    include_memory: bool = True
    """Si True, la memoria SQLite del agente se registra como fuente automáticamente."""

    top_k_per_source: int = 3
    """top_k global por fuente cuando no se override por fuente individual."""

    min_score: float = 0.5
    """min_score global cuando no se override por fuente individual."""

    max_total_chunks: int = 10
    """Límite duro de chunks totales tras el fan-out (ordenados por score desc)."""

    token_budget_warn_threshold: int = 4000
    """Umbral estimado de tokens totales (chunks + digest + skills). Si se supera,
    se emite un WARNING con el desglose. 0 = deshabilita la advertencia."""

    sources: list[KnowledgeSourceConfig] = []
    """Lista de fuentes de conocimiento externas configuradas."""


class DelegationConfig(BaseModel):
    """Config global de delegación (aplica a todos los agentes como valores por defecto)."""

    max_iterations_per_sub: int = 10
    timeout_seconds: int = 60


class AgentDelegationConfig(BaseModel):
    """Config de delegación por agente."""

    enabled: bool = False
    allowed_targets: list[str] = []


class AdminConfig(BaseModel):
    """Configuración del admin server del daemon."""

    port: int = 6497
    host: str = "127.0.0.1"
    auth_key: str | None = None
    chat_timeout: float = 300.0
    """Timeout en segundos para turnos de chat vía REST (POST /admin/chat/turn)."""


class UserConfig(BaseModel):
    """Preferencias del usuario."""

    timezone: str = ""
    """
    Timezone IANA (ej: "America/Argentina/Buenos_Aires").

    Si queda vacío, se autodetecta desde el host vía `tzlocal` con fallback a
    "UTC". Si el valor no es una zona IANA válida, se loggea un warning y se
    autodetecta igual.
    """

    @field_validator("timezone", mode="after")
    @classmethod
    def _resolve_timezone(cls, v: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        if v:
            try:
                ZoneInfo(v)
                return v
            except (ZoneInfoNotFoundError, ValueError):
                logger.warning(
                    "user.timezone='%s' no es una zona IANA válida — autodetectando",
                    v,
                )

        try:
            import tzlocal

            detected = tzlocal.get_localzone_name()
            if detected:
                logger.info("user.timezone autodetectado desde el host: %s", detected)
                return detected
        except Exception as exc:
            logger.warning("No se pudo autodetectar timezone del host: %s", exc)

        logger.info("user.timezone fallback a UTC")
        return "UTC"


# ---------------------------------------------------------------------------
# AgentConfig — config completa y resuelta para un agente
# ---------------------------------------------------------------------------


class AgentConfig(BaseModel):
    id: str
    name: str
    description: str
    system_prompt: str = ""
    """Prompt de sistema del agente. Opcional: si se omite, los sub-agentes de
    memoria (extractor/reconciliador) heredan el prompt hardcodeado por defecto
    del use case correspondiente. Un agente regular sin prompt corre con base
    vacía (responde sin instrucciones de sistema)."""
    llm: LLMConfig
    embedding: EmbeddingConfig
    memories: MemoriesConfig
    chat_history: ChatHistoryConfig
    skills: SkillsConfig = SkillsConfig()
    tools: ToolsConfig = ToolsConfig()
    semantic_routing: SemanticRoutingConfig = SemanticRoutingConfig()
    workspace: WorkspaceConfig = WorkspaceConfig()
    delegation: AgentDelegationConfig = AgentDelegationConfig()
    transcription: TranscriptionConfig | None = None
    channels: dict[str, dict[str, Any]] = {}
    providers: dict[str, ProviderConfig] = {}
    """Registry de proveedores post-merge. Heredado del global + overrides del agente."""


# ---------------------------------------------------------------------------
# GlobalConfig — config del sistema (sin agentes)
# ---------------------------------------------------------------------------


class FacesConfig(BaseModel):
    """Configuración del proveedor de reconocimiento facial (InsightFace)."""

    provider: Literal["insightface"] = "insightface"
    model: Literal["buffalo_sc", "buffalo_s", "buffalo_l"] = "buffalo_sc"
    match_threshold: float = 0.55
    """Score mínimo de similitud coseno para considerar una cara como MATCHED."""
    ambiguous_threshold: float = 0.40
    """Score entre ambiguous_threshold y match_threshold → cara AMBIGUOUS."""

    @model_validator(mode="after")
    def _validar_umbrales(self) -> "FacesConfig":
        if self.ambiguous_threshold >= self.match_threshold:
            raise ValueError(
                f"FacesConfig: ambiguous_threshold ({self.ambiguous_threshold}) "
                f"debe ser menor que match_threshold ({self.match_threshold})."
            )
        return self


class SceneConfig(BaseModel):
    """Configuración del proveedor de descripción de escena (LLM multimodal)."""

    provider: Literal["anthropic", "openai", "groq"] = "anthropic"
    model: str = "claude-sonnet-4-6"
    prompt_template: str | None = None
    """Prompt personalizado en español. None = usar el prompt built-in del adaptador."""
    api_key: str | None = None
    """API key del proveedor. Conviene en global.secrets.yaml bajo photos.scene.api_key."""


class DedupConfig(BaseModel):
    """Configuración del job nocturno de deduplicación de personas."""

    enabled: bool = True
    schedule: str = "0 3 * * *"
    """Expresión cron para el job de deduplicación. Validada por croniter."""
    similarity_threshold: float = 0.70
    """Score mínimo de similitud coseno entre centroides para reportar par duplicado."""


class PhotosConfig(BaseModel):
    """Configuración del pipeline de fotos (reconocimiento facial + escena)."""

    enabled: bool = True
    """Si False, el bot ignora todas las fotos con warning. No se carga ningún modelo."""
    enrollment_chats: Literal["private", "none"] = "private"
    """Tipos de chat donde el agente ofrecerá registrar caras nuevas.
    'private' = solo chats privados. 'none' = el agente nunca ofrece enrolar."""
    debug: bool = False
    """Si True, escribe /tmp/inaki.photo-debug.<timestamp>.log con el resultado del
    procesamiento y el prompt completo enviado al LLM. Útil para diagnosticar
    comportamientos extraños en grupos."""
    faces: FacesConfig = FacesConfig()
    scene: SceneConfig = SceneConfig()
    dedup: DedupConfig = DedupConfig()


class GlobalConfig(BaseModel):
    app: AppConfig
    llm: LLMConfig
    embedding: EmbeddingConfig
    memories: MemoriesConfig
    chat_history: ChatHistoryConfig
    channels: ChannelsGlobalConfig = ChannelsGlobalConfig()
    """Flags de presentación transversales a todos los canales. Solo global."""
    skills: SkillsConfig = SkillsConfig()
    tools: ToolsConfig = ToolsConfig()
    semantic_routing: SemanticRoutingConfig = SemanticRoutingConfig()
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    # default_factory (no `= SchedulerConfig()`): los campos RuntimePath se resuelven
    # contra `get_inaki_home()` en CADA instanciación de GlobalConfig (runtime, ya con el
    # home seteado), no al importar el módulo. Sin esto, `--home` no relocaliza la db si
    # el bloque `scheduler` falta del YAML. Vale para todo config con RuntimePath usado
    # como default de GlobalConfig/AgentConfig.
    workspace: WorkspaceConfig = WorkspaceConfig()
    delegation: DelegationConfig = DelegationConfig()
    admin: AdminConfig = AdminConfig()
    user: UserConfig = UserConfig()
    transcription: TranscriptionConfig | None = None
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)  # default_factory: ver nota en `scheduler` (RuntimePath en T7)
    photos: PhotosConfig | None = None
    """Configuración del pipeline de fotos. None = feature desactivada (no se carga nada)."""
    providers: dict[str, ProviderConfig] = {}
    """Registry top-level de proveedores — credenciales compartidas por vendor."""
