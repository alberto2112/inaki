"""Schema de configuración de Inaki — modelos Pydantic y helpers de path.

Solo declaraciones: sin I/O, sin carga de YAML. El loader (``config_loader``)
y la fachada (``config``) viven aparte. Importá desde ``infrastructure.config``
(fachada) salvo que necesites explícitamente solo el schema.
"""

from __future__ import annotations

import logging
from difflib import get_close_matches
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

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
# Base común de los modelos de configuración
# ---------------------------------------------------------------------------


class _ConfigBaseModel(BaseModel):
    """Base de TODOS los modelos de configuración del schema.

    Activa ``use_attribute_docstrings``: Pydantic captura el docstring que sigue
    a cada campo y lo expone como ``FieldInfo.description``. De este modo la
    ÚNICA fuente de verdad de la documentación de cada parámetro es su docstring
    acá — el setup TUI ya consume ``description`` (árbol de schema + modal de
    alta de campo/sección) para describir cada opción. Sin este flag, los 130+
    docstrings del schema no llegaban a la UI y había que leer el código para
    descubrir qué se podía configurar.

    Los ``model_config`` propios de las subclases (``extra="forbid"``,
    ``validate_default``, ``strict``...) se MERGEAN con este — no se pierden.

    Caveat de runtime: Pydantic lee la fuente vía ``inspect.getsource`` al
    definir la clase. Funciona con los ``.py`` presentes en disco (deploy actual:
    systemd + código fuente). Si en el futuro se empaqueta SIN fuentes (zipapp,
    solo ``.pyc``), revalidar que las descripciones se sigan poblando.

    Activa también ``extra="forbid"`` para TODO el schema: una clave que el
    modelo no declara es un typo del operador, y tragárselo en silencio hacía
    que el campo pareciera configurado sin estarlo. El validador de abajo se
    adelanta a Pydantic para nombrar la clave y sugerir la que quiso escribir.
    """

    model_config = ConfigDict(use_attribute_docstrings=True, extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _rechazar_claves_desconocidas(cls, data: Any) -> Any:
        """Convierte el "Extra inputs are not permitted" de Pydantic en algo accionable.

        Corre ANTES de la validación para poder nombrar el bloque, la clave
        sobrante y —cuando hay una parecida— el campo que el operador quiso
        escribir. El ``extra="forbid"`` de arriba queda como red: si un camino
        de construcción esquiva este validador, la clave se rechaza igual.
        """
        if not isinstance(data, dict):
            return data

        conocidas = set(cls.model_fields)
        desconocidas = [k for k in data if isinstance(k, str) and k not in conocidas]
        if not desconocidas:
            return data

        detalles = []
        for clave in sorted(desconocidas):
            parecidas = get_close_matches(clave, conocidas, n=1, cutoff=0.6)
            sugerencia = f" ¿Quisiste decir '{parecidas[0]}'?" if parecidas else ""
            detalles.append(f"'{clave}'{sugerencia}")

        validas = ", ".join(sorted(conocidas)) or "(ninguna)"
        raise ValueError(
            f"{cls.__name__}: clave(s) desconocida(s): {'; '.join(detalles)}. "
            f"Claves válidas: {validas}."
        )


# ---------------------------------------------------------------------------
# Sub-configs
# ---------------------------------------------------------------------------


class AppConfig(_ConfigBaseModel):
    """Arranque del proceso: identidad, logging, agente por defecto y extensiones.

    Bloque SOLO global (``global.yaml`` → ``app:``). No admite override
    per-agente: lo consumen el composition root (``inaki/cli.py``) y el
    ``AppContainer`` antes de que exista ningún agente.
    """

    name: str = "Inaki"
    """Nombre de la instancia del asistente.

    DECLARATIVO: ningún componente del runtime lo lee hoy — queda como etiqueta
    del despliegue para el operador. El nombre con el que el agente se presenta
    ante el LLM es ``AgentConfig.name``, no este."""

    log_level: str = "INFO"
    """Nivel mínimo de log del proceso: ``DEBUG``, ``INFO``, ``WARNING``, ``ERROR`` o ``CRITICAL``.

    Se resuelve contra el módulo ``logging`` sin distinguir mayúsculas; un valor
    no reconocido cae a ``INFO`` sin fallar. ``DEBUG`` agrega el detalle de los
    requests al provider, los embeddings y las tool calls."""

    ext_dirs: ExpandedPathList = ["ext", "~/.inaki/ext"]
    """Directorios donde se auto-descubren las extensiones de usuario, en orden.

    Cada directorio se escanea buscando ``*/manifest.py``, que registra tools,
    skills y fuentes de knowledge propias. Los paths relativos se resuelven
    contra el cwd del proceso; ``~`` se expande al cargar la config. Un
    directorio inexistente se saltea sin error."""

    default_agent: str = "general"
    """Agente que usan los comandos de CLI cuando no se pasa ``--agent``.

    Debe corresponder a un fichero ``agents/{id}.yaml`` existente. El validador
    de referencias cruzadas del TUI (``inaki setup``) avisa si apunta a un
    agente que no existe."""


class ProviderConfig(_ConfigBaseModel):
    """
    Entrada del registry top-level de proveedores.

    Cada entrada representa UN vendor (groq, openai, openrouter, ollama, etc.)
    con sus credenciales y endpoint. Las features (`llm`, `embedding`,
    `transcription`, `memories.llm`) referencian entradas por nombre vía su
    campo ``provider: <key>``, eliminando duplicación de ``api_key``/``base_url``.

    ``extra="forbid"`` atrapa typos temprano (``api_ky``).
    """

    model_config = ConfigDict(extra="forbid")

    type: str | None = None
    """Nombre del adapter que implementa este vendor. ``null`` → se usa la key del dict.

    Con ``providers.groq: {...}`` el tipo se resuelve solo a ``"groq"``. Solo se
    explicita para tener DOS entradas del mismo adapter con credenciales
    distintas: ``providers.groq-work: {type: groq, api_key: K2}`` deja que
    ``llm.provider: groq-work`` apunte al adapter groq con otra cuenta."""

    api_key: str | None = Field(default=None, json_schema_extra={"secret": True})
    """Credencial del vendor. Es un SECRETO: la TUI lo enmascara al editarlo.

    Opcional para los providers locales que no la piden (``ollama``, ``e5_onnx``);
    los adapters que sí la requieren fallan al construirse si falta."""

    base_url: str | None = None
    """Endpoint del vendor. ``null`` → el default hardcodeado del adapter.

    Obligatorio para servidores de inferencia propios OpenAI-compat (vLLM,
    llama.cpp), que no tienen default posible."""


_LLM_TIMEOUT_FALLBACK = 60


class LLMConfig(_ConfigBaseModel):
    """Cerebro conversacional del agente: qué modelo responde y con qué parámetros.

    Bloque per-agente: se declara en ``global.yaml`` como default de todos y se
    pisa campo a campo en ``agents/{id}.yaml``. NO lleva credenciales — ``provider``
    referencia una entrada del registry ``providers:``, de donde salen ``api_key``
    y ``base_url``.

    Los jobs de memoria pueden usar otro modelo sin tocar este bloque: ver
    ``memories.llm`` (``MemoryLLMConfig``), que lo hereda y lo override campo a campo.
    """

    provider: str = "openrouter"
    """KEY del registry ``providers:`` que aporta las credenciales y el endpoint.

    Adapters incluidos: ``openrouter``, ``openai``, ``openai_responses``,
    ``anthropic``, ``deepseek``, ``groq``, ``ollama``, ``custom``. Se
    auto-descubren por la constante ``PROVIDER_NAME`` del módulo, así que agregar
    uno nuevo es crear ``adapters/outbound/providers/{name}.py`` y declarar
    ``providers.{name}``."""

    model: str = "anthropic/claude-3-5-haiku"
    """Identificador del modelo, en el formato que espera el provider elegido.

    OpenRouter exige el prefijo del vendor (``anthropic/claude-3-5-haiku``); el
    adapter ``anthropic`` nativo NO lo lleva (``claude-sonnet-4-5``). Ollama usa
    el nombre local del modelo (``llama3.2``)."""

    temperature: float = 0.7
    """Aleatoriedad del muestreo: ``0.0`` determinista, valores altos más creativo.

    Orientativo: 0.3-0.5 para tareas de código o extracción, 0.7-0.9 para
    conversación. El adapter ``openai_responses`` la OMITE cuando
    ``reasoning_effort`` está seteado (la API devuelve 400 si van juntas)."""

    max_tokens: int = 2048
    """Techo de tokens que el modelo puede GENERAR en una respuesta.

    No acota el prompt de entrada. Subirlo para respuestas largas (código,
    análisis). Cuidado con servidores locales OpenAI-compat: muchos validan
    ``prompt + max_tokens > n_ctx`` y devuelven HTTP 400 — contra una ventana
    chica hay que bajarlo junto con ``chat_history.max_messages``, y hacerlo en
    el AGENTE que usa ese provider, no en el global."""

    reasoning_effort: str | None = None
    """Intensidad del modo razonamiento (thinking). ``null`` = desactivado.

    Cada adapter lo traduce a su dialecto: Groq lo manda tal cual y cambia
    ``max_tokens`` por ``max_completion_tokens``; ``anthropic`` lo convierte en
    el ``budget_tokens`` del extended thinking; ``openai_responses`` lo manda
    como ``reasoning.effort`` y omite ``temperature``. Con thinking activo
    conviene subir ``timeout_seconds``, y es el flag del que depende
    ``channels.thinking_indicator``."""

    timeout_seconds: int = Field(default=_LLM_TIMEOUT_FALLBACK, gt=0)
    """Timeout HTTP del request al provider, en segundos.

    Default ``60``. Recomendado subirlo (180-300) cuando se usa thinking mode
    sobre queries complejas, donde el modelo puede tardar mucho más en
    responder.

    Un valor no parseable o ``<= 0`` es un error de config, no algo que
    sanitizar: hasta la Fase 4 del refactor esto caía al fallback de 60s en
    silencio, así que ``timeout_seconds: "sesenta"`` corría con 60 y el
    operador creía haber configurado otra cosa.
    """

    request_delay_seconds: float = Field(default=2.0, ge=0)
    """Espera mínima (segundos) ANTES de cada llamada al provider dentro del
    loop agéntico, EXCEPTO la primera del turno.

    Default ``2.0``. Evita saturar el rate limiter del provider cuando el modelo
    encadena varias tool calls en un mismo turno (cada iteración del loop es un
    ``llm.complete()``): sin throttle, 5 tool calls disparan 5 requests
    back-to-back. La primera llamada del turno NO se demora (sería latencia pura
    sin proteger nada — el rate limiter se satura por las llamadas encadenadas).

    ``0`` desactiva el throttle. Un negativo o un valor no parseable es un
    error de config (antes se clampeaban o caían al default en silencio).
    """


class EmbeddingConfig(_ConfigBaseModel):
    """Modelo de embeddings que alimenta todo el retrieval del sistema.

    Un solo vectorizador sirve a tres consumidores: el semantic routing de tools
    y skills, la búsqueda de memoria y el pre-fetch de knowledge. Bloque
    per-agente, con registry ``providers:`` para las credenciales (el default
    ``e5_onnx`` es local y no necesita ninguna).

    ⚠ Cambiar de modelo (o de ``dimension``) invalida TODOS los vectores ya
    persistidos: no hay auto-migración — hay que borrar y recrear la DB de
    memoria y los índices de knowledge.
    """

    model_config = ConfigDict(validate_default=True)  # RuntimePath en los defaults

    provider: str = "e5_onnx"
    """KEY del registry ``providers:`` que vectoriza. ``e5_onnx`` (local) u ``openai``.

    ``e5_onnx`` corre multilingual-e5-small con ONNX Runtime en la propia
    máquina: sin red, sin costo y sin entrada en ``providers:`` — es lo
    recomendado en la Pi 5. ``openai`` requiere ``providers.openai.api_key``."""

    model_dirname: RuntimePath = "models/e5-small"
    """Directorio con los ficheros del modelo ONNX. SOLO lo usa ``e5_onnx``.

    Espera adentro ``model.onnx`` y ``tokenizer.json`` (descargables de
    HuggingFace: ``intfloat/multilingual-e5-small``). Relativo al home de
    instancia; se reancla con ``--home`` / ``INAKI_HOME``. Un path absoluto se
    usa tal cual."""

    model: str = "text-embedding-3-small"
    """Nombre del modelo remoto. SOLO lo usa el provider ``openai``; ``e5_onnx`` lo ignora."""

    dimension: int = 384
    """Dimensión del vector de embedding. Debe coincidir con la del modelo.

    ``multilingual-e5-small`` produce 384 y no admite otro valor. Con el provider
    ``openai`` viaja como parámetro ``dimensions`` del request, que sí recorta el
    vector. Forma parte de la clave del cache
    ``(content_hash, provider, dimension)``, así que cambiarla invalida las
    entradas viejas sin colisionar — pero NO migra las DB de vectores ya escritas."""

    cache_filename: RuntimePath = "data/embedding_cache.db"
    """Fichero SQLite del cache de embeddings, para no re-vectorizar texto repetido.

    Relativo al home de instancia; se reancla con ``--home`` / ``INAKI_HOME``. Es
    un cache puro: borrarlo solo cuesta recalcular."""


class TranscriptionConfig(_ConfigBaseModel):
    """Config del provider de transcripción de audio (opcional)."""

    provider: str = "groq"
    """KEY del registry ``providers:`` que transcribe (endpoint Whisper OpenAI-compat).

    El bloque entero es opcional, pero si un canal tiene ``voice_enabled: true``
    y no hay ``transcription:`` (ni en el agente ni en el global), el arranque
    del agente falla con un error explícito en vez de ignorar los audios."""

    model: str = "whisper-large-v3-turbo"
    """Modelo de transcripción, en el nombre que espera el provider."""

    language: str | None = None
    """Idioma esperado del audio en ISO-639-1 (``"es"``, ``"en"``). ``null`` → autodetect.

    Es solo el default: el canal puede pasar un idioma por llamada y ese gana.
    Fijarlo mejora la precisión cuando se sabe que el audio siempre viene en un
    idioma; una cadena vacía no se manda al provider."""

    timeout_seconds: int = 60
    """Timeout HTTP del request de transcripción, en segundos."""

    max_audio_mb: int = 25
    """Tamaño máximo del audio en MB. Un fichero mayor se rechaza ANTES de subirlo.

    El límite se chequea localmente y levanta ``TranscriptionFileTooLargeError``
    sin gastar red ni cuota. El default ``25`` es el techo del endpoint de Groq
    Whisper — subirlo por encima de lo que acepta el provider solo cambia dónde
    falla."""


# Los DTOs ``Resolved*Config`` (feature + creds compuestas) viven en la capa
# adapters — cada familia los declara en su ``base.py`` (providers, embedding,
# transcription). Las factories de infrastructure los componen desde acá.


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
    superior. Ver ``AgentContainer._resolve_memories_llm`` (container.py).

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
    # que solo core consume) vive en core/domain/value_objects/agent_settings.py
    # (``MemorySettings``). El container traduce este modelo a ese VO.

    def merged_llm_config(self, base: LLMConfig) -> LLMConfig:
        """
        Devuelve la ``LLMConfig`` efectiva (sin creds) tras aplicar el override
        compartido ``memories.llm``.

        Merge field-by-field: los campos que el usuario seteó EXPLÍCITAMENTE
        en ``memories.llm.*`` (incluso ``null``) pisan al ``base``; el resto hereda.
        Si no hay override, devuelve el ``base`` tal cual.

        ÚNICA excepción declarada al motor de merge del dominio
        (``core/domain/config_merge``), y a propósito: el motor opera sobre dicts
        CRUDOS antes de validar, y este merge ocurre DESPUÉS, entre dos modelos ya
        validados. La semántica es la misma —``model_fields_set`` es el
        equivalente pydantic de "la clave está escrita en el YAML", así que
        ausente hereda y ``null`` explícito pisa— pero expresada en el mundo de
        los modelos. Absorberlo obligaría a mergear ``llm`` y ``memories.llm`` en
        crudo antes de validar, lo que convertiría ``MemoriesConfig.llm`` en un
        ``LLMConfig`` y rompería el tri-estado que el setup TUI edita sobre
        ``memories.llm.*``. No vale el cambio: cualquier ajuste a la semántica de
        merge se hace en el motor y se replica acá.

        Las credenciales se resuelven aparte contra el registry ``providers``
        — la composición del ``ResolvedLLMConfig`` (DTO de adapters) vive en
        ``AgentContainer._resolve_memories_llm``.
        """
        if self.llm is None:
            return base

        fields_set = self.llm.model_fields_set
        overrides = {f: getattr(self.llm, f) for f in fields_set}
        return base.model_copy(update=overrides)


class ChatHistoryConfig(_ConfigBaseModel):
    """Memoria a CORTO plazo: qué conversación previa ve el LLM en cada turno.

    Bloque per-agente. Es la ventana deslizante que se inyecta al prompt, distinta
    de ``memories`` (memoria a largo plazo, destilada por el job nocturno).

    Los dos knobs de acá gobiernan el costo de contexto de cada turno: cuánto
    historial entra (``max_messages``) y cuánto rastro de tools se guarda
    (``persist_tool_calls`` / ``persist_tool_result_max_chars``).
    """

    model_config = ConfigDict(validate_default=True)  # RuntimePath en los defaults

    db_filename: RuntimePath = "data/history.db"
    """Fichero SQLite del historial de conversación.

    Relativo al home de instancia; se reancla con ``--home`` / ``INAKI_HOME``. Un
    path absoluto se usa tal cual. Los agentes que comparten fichero se aíslan por
    ``agent_id``; para aislamiento FÍSICO, dale a cada agente su propio
    ``db_filename``."""

    max_messages: int = 0
    """Últimos N mensajes del scope que se inyectan al LLM. ``0`` = sin límite.

    Es una ventana deslizante sobre la lectura, no una política de borrado: los
    mensajes viejos siguen en la DB. Bajarlo es la palanca directa para recortar
    tokens de prompt contra modelos con ventana chica. OJO: el conteo dentro de
    una ventana llena no sirve para detectar mensajes nuevos — el drain in-flight
    cursa por rowid monotónico justamente por eso."""

    merge_chats: bool = False
    """Política de aislamiento del historial. ``False`` = un hilo por ``(channel, chat_id)``.

    Con ``False`` (default) el agente solo ve los mensajes de la conversación
    actual: privado de Telegram, grupo y CLI quedan separados. Con ``True``
    comparte un único historial entre todos sus canales y chats — útil para que
    lo hablado en privado esté disponible al responder en grupo, a costa de
    filtrar contexto entre conversaciones."""

    persist_tool_calls: bool = True
    """Persistir el par assistant+tool_calls ↔ tool_results en el historial.

    Default ``True``: el agente principal tiene memoria episódica de sus propias
    acciones entre turnos (no olvida en qué path escribió con ``write_file``, ni
    qué ficheros ya mandó). Con ``False`` el rastro vive solo en el tool loop del
    turno y se descarta — el agente queda amnésico de su actividad con
    herramientas, que es lo que producía el patrón "afirmo haber hecho algo que
    no recuerdo haber hecho". Solo afecta al agente principal; los subagentes
    one-shot quedan afuera por diseño. Ver las notas de migración
    ``persist-tool-calls`` y ``outbound-send-single-owner`` en ``CLAUDE.md``."""

    persist_tool_result_max_chars: int = 2000
    """Truncación (en chars) de cada tool result al persistirlo con
    ``persist_tool_calls``. Acota el costo de contexto y disco cuando una tool
    devuelve un volcado grande (web_search, RAG). ``0`` = sin truncar. El turno
    en curso siempre ve el result completo; solo la copia persistida se recorta."""


class ChannelsGlobalConfig(_ConfigBaseModel):
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


class ChannelFallbackConfig(_ConfigBaseModel):
    """Config de fallbacks para el routing de canales del scheduler.

    Cuando una task dispara un envío a un canal que no tiene sink nativo
    (p. ej. ``cli``, ``rest``, ``daemon``), el ``ChannelRouter`` resuelve
    el destino efectivo aplicando esta cascada:

      1. Sink nativo registrado para el prefix del target.
      2. Entry en ``overrides`` para el ``channel_type`` del target.
      3. ``default`` global (si está configurado).
      4. Fallback hardcoded: ``file://~/.inaki/data/scheduler-fallback.log``.
    """

    default: str | None = None
    """Target al que van los canales sin sink nativo ni override. ``null`` → log de fallback.

    Es un target string con prefijo: ``"telegram:12345"``, ``"file:///var/log/x.log"``
    o ``"null:"`` para descartar. Se aplica DESPUÉS de ``overrides``, así que
    funciona como red general; con ``null`` el envío cae al
    ``scheduler.fallback_log_filename``."""

    overrides: dict[str, str] = {}
    """Redirecciones ``channel_type → target string``, evaluadas antes que ``default``.

    Solo se consultan para prefijos SIN sink nativo registrado — un target de un
    canal vivo nunca se redirige. Ejemplo: ``{"cli": "telegram:123"}`` manda a ese
    chat las salidas de tareas que nacieron en la CLI, que si no nadie leería."""


class SchedulerConfig(_ConfigBaseModel):
    """Motor de tareas programadas: cron, one-shots, reintentos y routing de la salida.

    Recurso HARNESS-GLOBAL: se declara SOLO en ``global.yaml`` y el
    ``AppContainer`` construye una única instancia compartida por todos los
    agentes — no hay ni puede haber un scheduler per-agente. Para aislar
    agendas hay que levantar otra instancia del arnés con su propio
    ``--home`` / ``INAKI_HOME``.

    Solo corre bajo ``inaki daemon``: en la CLI interactiva no hay proceso vivo
    que dispare nada. Los límites de acá (``max_retries``, ``max_tasks_per_agent``,
    ``output_truncation_size``) son las barandas contra un agente que programa
    de más o contra una tarea que falla en loop.
    """

    model_config = ConfigDict(validate_default=True)  # RuntimePath en los defaults

    enabled: bool = True
    """Kill-switch del scheduler. ``False`` = el daemon no arranca el loop de tareas.

    Con ``False`` tampoco se reconcilian las tareas builtin (consolidación,
    reconciliación, dedup de caras): quedan declaradas pero nadie las dispara."""

    db_filename: RuntimePath = "data/scheduler.db"
    """Fichero SQLite con las tareas programadas y su estado de ejecución.

    Relativo al home de instancia; se reancla con ``--home`` / ``INAKI_HOME``. Un
    path absoluto se usa tal cual. Es el mismo fichero que lee ``inaki scheduler``
    desde la CLI."""

    fallback_log_filename: RuntimePath = "data/scheduler-fallback.log"
    """Fallback de último recurso del router de dispatch (cascada). Relativo al home de
    instancia; se reancla con ``--home`` / ``INAKI_HOME``. El composition root lo envuelve
    en ``file://`` y lo inyecta al ``ChannelRouter`` (por privacidad, bajo ``<home>/data/``)."""
    max_retries: int = 3
    """Reintentos de una tarea que falla, ADEMÁS del intento inicial.

    Con el default ``3``, una tarea rota se ejecuta hasta 4 veces. ``0`` =
    un solo intento. Los negativos se saturan a ``0``."""

    retry_backoff_seconds: float = 10.0
    """Base de la espera entre reintentos, en segundos. La progresión es LINEAL.

    El intento N espera ``retry_backoff_seconds * N``: con el default ``10.0``,
    las esperas son 10s, 20s, 30s. ``0`` reintenta sin pausa. Los negativos se
    saturan a ``0``."""

    max_tasks_per_agent: int = 20
    """Techo de tareas ACTIVAS (pending o running) que un agente puede tener a la vez.

    Al llegar al límite, crear otra falla con ``TooManyActiveTasksError`` en vez
    de aceptarla — es la baranda contra un LLM que programa en loop. No cuenta
    las tareas ya completadas ni las canceladas, y no aplica a las tareas sin
    agente creador. El mínimo efectivo es ``1``."""

    output_truncation_size: int = 65536
    """Truncación (en chars) del output de una tarea al guardarlo en su registro de ejecución.

    Acota lo que un ``shell_exec`` verborrágico puede escribir en la DB del
    scheduler. Solo afecta a la copia persistida."""

    channel_fallback: ChannelFallbackConfig = ChannelFallbackConfig()
    """Adónde va la salida de una tarea cuyo canal destino no tiene sink vivo.

    Agrupa el ``default`` global y los ``overrides`` por tipo de canal. Sin
    configurar, todo cae al ``fallback_log_filename``."""


class SkillsConfig(_ConfigBaseModel):
    """Selección RAG de SKILLS: qué instrucciones se inyectan al prompt en cada turno.

    Una skill es un bloque de instrucciones en markdown. Cuando el agente tiene
    pocas, entran todas; a partir de ``semantic_routing_min_skills`` se
    seleccionan por similitud contra el embedding de la consulta del usuario, y
    ``sticky_ttl`` evita que una skill recién elegida desaparezca al turno
    siguiente porque el usuario escribió "dale".

    Bloque hermano de ``tools``, con la MISMA mecánica pero presupuesto propio: acá
    se paga en tokens de prompt, allá en schemas ofrecidos al LLM. Las políticas
    comunes a ambos pipelines viven en ``semantic_routing``.
    """

    semantic_routing_min_skills: int = 10
    """Nº de skills a partir del cual se ACTIVA el routing. Con menos o igual, entran todas.

    Es un umbral de activación, no un mínimo de resultados: con
    ``len(skills) <= min_skills`` el agente recibe el catálogo completo y no se
    calcula ningún embedding. Subirlo posterga el routing; bajarlo lo enciende
    antes."""

    semantic_routing_top_k: int = 3
    """Máximo de skills que el retrieval devuelve por turno (default más chico que en ``tools``).

    Cada skill inyecta su texto de instrucciones al system prompt, así que este
    número se paga directo en tokens: por eso el default es 3 y no 5 como el de
    tools. Las que sigan vivas por ``sticky_ttl`` se SUMAN a estas — el techo real
    del turno es mayor que ``top_k``."""

    semantic_routing_min_score: float = 0.0
    """Similitud coseno mínima para que una skill entre en la selección. ``0.0`` = sin piso.

    Con el default, ``top_k`` manda solo y siempre se devuelven las mejores
    aunque encajen poco. Subirlo hace que un turno sin skill relevante no
    arrastre ninguna."""

    sticky_ttl: int = 3
    """Turnos que una skill seleccionada sigue viva sin volver a ser elegida. ``0`` = desactivado.

    Al ser reseleccionada, el contador vuelve al valor completo; si no, decrementa
    y a cero se cae. Es lo que sostiene el hilo cuando el usuario responde con una
    frase corta que ya no matchea la skill. Con ``0`` no se guarda estado sticky y
    cada turno arranca de cero."""


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


ContainmentMode = Literal["strict", "warn", "off"]


class WorkspaceConfig(_ConfigBaseModel):
    """
    Workspace sobre el que operan las tools de filesystem.

    Define el sandbox de ``read_file``, ``write_file``, ``patch_file`` y
    ``edit_file``: dónde resuelven los paths relativos y qué pasa cuando el LLM
    pide uno que se sale. Bloque per-agente — darle a cada agente su propio
    ``path`` los mantiene separados en disco.
    """

    path: ExpandedPath = "~/inaki-workspace"
    """Directorio raíz contra el que las tools de filesystem resuelven los paths relativos.

    Es también la frontera que aplica ``containment``. ``~`` se expande al cargar
    la config."""

    containment: ContainmentMode = "strict"
    """Qué hacer cuando un path resuelto se sale del workspace (absoluto o vía ``..``).

    - ``strict`` (default, recomendado en producción) → aborta con
      ``WorkspaceEscapeError``; la tool nunca toca el fichero.
    - ``warn`` → permite el acceso y deja un WARNING en el log.
    - ``off`` → sin chequeo; las tools pueden leer y escribir en cualquier lado
      al que llegue el proceso."""

    def model_post_init(self, __context: object) -> None:
        # Expand ~ in the default value (BeforeValidator no corre en defaults de clase).
        object.__setattr__(self, "path", str(Path(self.path).expanduser()))


class BroadcastServerConfig(_ConfigBaseModel):
    """Rol **server** del broadcast: esta instancia escucha conexiones entrantes."""

    port: int = Field(ge=1024, le=65535)
    """Puerto TCP en el que escucha el servidor (1024..65535). Escucha en todas
    las interfaces de la LAN (``0.0.0.0``)."""


class BroadcastClientConfig(_ConfigBaseModel):
    """Rol **client** del broadcast: esta instancia se conecta a un server remoto."""

    host: str
    """Dirección IP o hostname del servidor (sin puerto — ese va en ``port``)."""

    port: int = Field(ge=1024, le=65535)
    """Puerto TCP del servidor remoto (1024..65535)."""


class BroadcastEmitConfig(_ConfigBaseModel):
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


class BroadcastConfig(_ConfigBaseModel):
    """
    Config del **transporte** de broadcast TCP entre instancias de Inaki.

    Esta clase modela SOLO la capa de red (topología + emisión de eventos). El
    **comportamiento del bot en grupos** (``behavior``, ``bot_username``,
    ``rate_limiter``, ``rate_limiter_window``) NO vive acá: vive en
    ``TelegramGroupsConfig`` (``channels.telegram.groups``), porque aplica a
    cualquier grupo — haya o no broadcast TCP activo. Mezclar ambos forzaba a
    levantar el transporte solo para configurar cómo responde el bot.

    El rol se declara con bloques nombrados: ``server`` (esta instancia escucha)
    XOR ``client`` (esta instancia se conecta). El rol y sus campos son la misma
    cosa — no existe un ``mode`` aparte que pueda desincronizarse del bloque.
    ``auth`` es el secreto HMAC compartido, único para ambos roles (el del
    client DEBE coincidir con el del server).

    Validaciones (solo con ``enabled=True``; apagado no se exige topología):
    - ``server`` y ``client`` son mutuamente excluyentes, y uno debe estar.
    - ``auth`` es obligatorio.
    - Los rangos de puerto (1024..65535) los validan los sub-modelos.
    """

    enabled: bool = True
    """Kill-switch del transporte. ``False`` = el bloque queda escrito pero no se
    levanta ningún adapter TCP (y no se exige topología ni auth)."""

    auth: str | None = Field(default=None, json_schema_extra={"secret": True})
    """Secreto HMAC-SHA256 compartido entre server y clients. Obligatorio cuando
    ``enabled=True`` (default)."""

    server: BroadcastServerConfig | None = None
    """Rol server: esta instancia escucha en ``server.port``. XOR con ``client``."""

    client: BroadcastClientConfig | None = None
    """Rol client: esta instancia se conecta a ``client.host:client.port``. XOR con ``server``."""

    emit: BroadcastEmitConfig = BroadcastEmitConfig()
    """Flags que controlan qué tipos de eventos se emiten al broadcast.
    Sin override usa los defaults: solo ``assistant_response`` activo."""

    @model_validator(mode="after")
    def _validar_topologia(self) -> "BroadcastConfig":
        """Valida server XOR client + auth obligatorio. Con ``enabled=False`` no
        se exige nada: el bloque puede quedar incompleto mientras está apagado."""
        if not self.enabled:
            return self

        tiene_server = self.server is not None
        tiene_client = self.client is not None

        if tiene_server and tiene_client:
            raise ValueError(
                "BroadcastConfig: 'server' y 'client' son mutuamente excluyentes — "
                "un nodo no puede ser servidor y cliente simultáneamente."
            )

        if not tiene_server and not tiene_client:
            raise ValueError(
                "BroadcastConfig: debe definirse el bloque 'server' (esta instancia "
                "escucha) o 'client' (esta instancia se conecta) — no pueden estar "
                "ambos ausentes. Para apagar el transporte sin borrar el bloque, "
                "usá 'enabled: false'."
            )

        if self.auth is None:
            raise ValueError("BroadcastConfig: 'auth' (secreto HMAC compartido) es obligatorio.")

        return self


class TelegramGroupsConfig(_ConfigBaseModel):
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


class CliChannelConfig(_ConfigBaseModel):
    """
    Config tipada del canal CLI/REST.

    Es el bloque ``channels.cli`` que consume el admin server al armar el
    ``ChannelContext`` de un turno conversacional sin canal de mensajería.
    """

    user: str | None = None
    """Identidad ESTABLE del turno CLI/REST. Se usa como ``user_id`` y como
    ``context_id`` (nombra ``~/.inaki/users/cli/{user}.md``) y puebla
    ``{{CHANNEL.USERNAME}}``. ``None`` → el ``context_id`` es el ``session_id``
    (UUID efímero por proceso, sin fichero pre-escribible)."""


class TelegramChannelConfig(_ConfigBaseModel):
    """
    Config tipada del canal Telegram.

    Tuvo ``extra="allow"`` mientras el bloque no se validaba al cargar: sin
    validación, rechazar lo desconocido habría roto configs sin dar un
    diagnóstico útil. Desde que el canal se valida contra ``CHANNEL_SCHEMAS``,
    un campo que no está acá es un typo y se rechaza como en el resto del schema.
    """

    token: str = Field(default="", json_schema_extra={"secret": True})
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


class KnowledgeSourceConfig(_ConfigBaseModel):
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


class KnowledgeConfig(_ConfigBaseModel):
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


class DelegationConfig(_ConfigBaseModel):
    """Config global de delegación (aplica a todos los agentes como valores por defecto)."""

    max_iterations_per_sub: int = 10
    """Vueltas máximas del tool loop que puede gastar UNA llamada delegada.

    Equivalente de ``tools.tool_call_max_iterations`` para el turno one-shot del
    sub-agente, y con default más generoso (10 vs 5): al sub se le delega una
    tarea completa, no un intercambio conversacional."""

    timeout_seconds: int = 60
    """Presupuesto de reloj de una llamada delegada, en segundos.

    Se aplica como ``asyncio.wait_for`` sobre el turno del sub-agente: al
    vencerse, la delegación se corta y el caller recibe el timeout como
    resultado. Es un techo de tiempo real, independiente de
    ``max_iterations_per_sub``, que cuenta vueltas."""


class AgentDelegationConfig(_ConfigBaseModel):
    """Config de delegación por agente."""

    enabled: bool = False
    """Habilita la tool ``delegate`` para ESTE agente. Opt-in.

    Con ``False`` (default) la tool no se registra siquiera: no aparece en los
    schemas y el modelo no puede razonar sobre ella. Encenderlo también inyecta
    al system prompt el bloque de descubrimiento con los agentes disponibles y
    sus tools."""

    allowed_targets: list[str] = []
    """Allow-list de sub-agentes a los que delegar. Lista vacía = todos los disponibles.

    Se INTERSECA con los sub-agentes registrados (``agents/sub-agents/*.yaml``):
    nunca amplía el universo, solo lo recorta — un id que no existe se ignora.
    Filtra a la vez los destinos que la tool acepta y los que se anuncian en el
    bloque de descubrimiento del prompt. Si la intersección queda vacía, la tool
    ``delegate`` no se registra."""


class AdminConfig(_ConfigBaseModel):
    """Configuración del admin server del daemon."""

    port: int = 6497
    """Puerto TCP en el que escucha el admin server del daemon.

    Es también el puerto al que apunta la CLI para hablar con el daemon local
    (``inaki chat``, ``inaki tool``, ``inaki scheduler``)."""

    host: str = "127.0.0.1"
    """Interfaz en la que bindea el admin server.

    El default ``127.0.0.1`` lo deja accesible SOLO desde la propia máquina.
    Ponerlo en ``0.0.0.0`` lo expone a la LAN: hacelo únicamente con ``auth_key``
    configurada."""

    auth_key: str | None = Field(default=None, json_schema_extra={"secret": True})
    """Credencial del header ``X-Admin-Key`` que protege los endpoints de gestión.

    Es un SECRETO: la TUI lo enmascara. Con ``null`` (default) el daemon arranca
    igual pero loggea un WARNING y los endpoints protegidos responden 403 — o
    sea, sin clave no se administra. La CLI la toma de acá salvo que se le pase
    ``--remote-key``."""

    chat_timeout: float = 300.0
    """Timeout en segundos para turnos de chat vía REST (POST /admin/chat/turn)."""


class UserConfig(_ConfigBaseModel):
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
# Registry de canales — fuente única de "qué canal existe y con qué schema"
# ---------------------------------------------------------------------------

CHANNEL_SCHEMAS: dict[str, type[BaseModel]] = {
    "telegram": TelegramChannelConfig,
    "cli": CliChannelConfig,
}
"""Schema de cada canal soportado, indexado por su clave en ``channels:``.

Fuente ÚNICA de la verdad, consumida por tres superficies que antes la
duplicaban o la ignoraban: la validación de ``AgentConfig.channels`` (acá
abajo), la introspección del setup TUI (inyectada desde ``inaki/setup_cli.py``)
y el generador de ``docs/config-reference.md``.

Vive en este módulo, y no en uno propio, para no crear un ciclo de imports:
las clases que indexa se definen arriba. Agregar un canal = agregar su modelo
y una entrada acá; **no** hay más lugares que tocar.
"""


# ---------------------------------------------------------------------------
# AgentConfig — config completa y resuelta para un agente
# ---------------------------------------------------------------------------


class AgentConfig(_ConfigBaseModel):
    """Config completa y RESUELTA de un agente — el resultado del merge, no un fichero.

    Lo que el operador escribe en ``agents/{id}.yaml`` es un DELTA: cada bloque
    que declara pisa campo a campo al homónimo de ``global.yaml``, y lo que no
    menciona se hereda. Este modelo es lo que queda después de ese merge, y es lo
    único que ve el ``AgentContainer`` al construir el agente.

    Solo ``id``, ``name`` y ``description`` son obligatorios y exclusivos del
    agente: no tienen contraparte global de la que heredar.

    Acá viven únicamente los recursos del tier PER-AGENTE (``llm``, ``embedding``,
    ``memories``, ``chat_history``, ``channels``). Los harness-global
    (``scheduler``, ``knowledge``, ``photos``) no tienen campo en este modelo a
    propósito: declararlos en el YAML de un agente es un error de clave, no un
    override silencioso.

    Los use cases NO reciben este objeto: ``container.py`` lo traduce a Settings
    VOs (``core/domain/value_objects/agent_settings.py``) para que el dominio no
    dependa del schema de infraestructura.
    """

    id: str
    """Identificador técnico del agente. Debe coincidir con el nombre del fichero YAML.

    El registry indexa por el NOMBRE DEL FICHERO (``agents/general.yaml`` →
    ``general``), pero este campo es el que viaja como ``agent_id`` a todo el
    runtime: scoping del historial y la memoria, ``created_by`` de las tareas del
    scheduler y target de las delegaciones. Si difiere del nombre del fichero,
    la config se carga por un id y los datos se guardan bajo otro."""

    name: str
    """Nombre visible del agente, para humanos.

    Va al prompt del propio agente (así sabe cómo se llama) y aparece junto al id
    en el bloque de descubrimiento de los agentes que pueden delegarle."""

    description: str
    """Qué hace el agente y en qué se especializa — en una frase.

    NO es un comentario: se INYECTA en el system prompt de los OTROS agentes, en
    el bloque «Available agents for delegation», y es lo único que el caller lee
    para decidir a quién delegarle una tarea. Una descripción vaga produce
    delegaciones al agente equivocado. Se colapsa a una sola línea, así que un
    bloque multilínea (``|``) igual termina plano."""

    system_prompt: str = ""
    """Prompt de sistema del agente. Opcional: si se omite, los sub-agentes de
    memoria (extractor/reconciliador) heredan el prompt hardcodeado por defecto
    del use case correspondiente. Un agente regular sin prompt corre con base
    vacía (responde sin instrucciones de sistema)."""
    llm: LLMConfig
    """Modelo conversacional de este agente. Hereda del ``llm`` global campo a campo."""

    embedding: EmbeddingConfig
    """Vectorizador de este agente (routing, memoria, knowledge). Hereda del global campo a campo."""

    memories: MemoriesConfig
    """Memoria a largo plazo de este agente: store, digest y los dos jobs nocturnos.

    Recurso per-agente: acá viven los flags ``enabled`` de consolidación y
    reconciliación, que SOLO tienen efecto declarados en el agente."""

    chat_history: ChatHistoryConfig
    """Historial de conversación a corto plazo de este agente y su ventana hacia el LLM."""

    skills: SkillsConfig = SkillsConfig()
    """Selección RAG de las skills de este agente. Hereda del bloque global campo a campo."""

    tools: ToolsConfig = ToolsConfig()
    """Selección y ejecución de tools de este agente.

    Además de heredar el bloque global, es donde un SUB-agente declara
    ``tools.allowed`` para restringir qué tools del caller puede usar."""

    semantic_routing: SemanticRoutingConfig = SemanticRoutingConfig()
    """Políticas de routing comunes a skills y tools de este agente."""

    workspace: WorkspaceConfig = WorkspaceConfig()
    """Sandbox de filesystem de este agente. Un ``path`` propio lo aísla de los demás."""

    delegation: AgentDelegationConfig = AgentDelegationConfig()
    """Si este agente puede delegar y a qué sub-agentes. Opt-in, per-agente.

    Los presupuestos de una delegación (iteraciones, timeout) NO viven acá: son
    globales (``GlobalConfig.delegation``)."""

    transcription: TranscriptionConfig | None = None
    """Provider de transcripción de audio de este agente. ``None`` → hereda el global.

    Si un canal del agente tiene ``voice_enabled: true`` y no hay bloque ni acá
    ni en el global, el agente falla al construirse en vez de ignorar los audios."""

    channels: dict[str, Any] = {}
    """Adapters de canal del agente, indexados por su clave en ``channels:``.

    Los bloques de canales conocidos (``CHANNEL_SCHEMAS``) llegan acá **ya
    validados y coercionados a su modelo Pydantic** por ``_validar_channels``.
    Para acceso tipado usá las properties (``telegram``, ``cli``); el dict
    directo sirve para iterar o preguntar qué canales declaró el agente.
    """
    providers: dict[str, ProviderConfig] = {}
    """Registry de proveedores post-merge. Heredado del global + overrides del agente."""

    @field_validator("channels", mode="before")
    @classmethod
    def _validar_channels(cls, value: Any) -> Any:
        """Valida cada bloque de canal conocido contra ``CHANNEL_SCHEMAS``.

        Es la ÚNICA puerta de validación de canales, y por eso vive acá y no en
        el loader: cubre por igual los cuatro caminos que construyen un
        ``AgentConfig`` (``load_agent_config``, el builder efímero del flujo
        delegate, el admin server y los tests). Antes ``channels`` era un
        ``dict[str, dict[str, Any]]`` opaco y sus 26 campos —el 14% del schema—
        no se validaban NUNCA al cargar: un typo o un tipo mal puesto viajaba
        hasta el primer uso en runtime, o se comía un default silencioso.

        Un canal desconocido es un error, no un bloque inerte: pasa lo mismo
        que con un typo de campo, el operador escribió algo que nadie lee.
        """
        if not isinstance(value, dict):
            return value

        resultado: dict[str, Any] = {}
        for nombre, bloque in value.items():
            schema = CHANNEL_SCHEMAS.get(nombre)
            if schema is None:
                conocidos = ", ".join(sorted(CHANNEL_SCHEMAS))
                raise ValueError(
                    f"channels.{nombre}: canal desconocido. Canales soportados: {conocidos}."
                )
            if isinstance(bloque, schema):
                resultado[nombre] = bloque
                continue
            if bloque is None:
                bloque = {}
            if not isinstance(bloque, dict):
                raise ValueError(
                    f"channels.{nombre}: se esperaba un bloque de config (mapa), "
                    f"recibido {type(bloque).__name__}."
                )
            try:
                resultado[nombre] = schema.model_validate(bloque)
            except ValidationError as exc:
                raise ValueError(f"channels.{nombre}: {exc}") from exc
        return resultado

    @property
    def telegram(self) -> TelegramChannelConfig | None:
        """Bloque ``channels.telegram`` tipado, o ``None`` si el agente no lo declara."""
        bloque = self.channels.get("telegram")
        return bloque if isinstance(bloque, TelegramChannelConfig) else None

    @property
    def cli(self) -> CliChannelConfig | None:
        """Bloque ``channels.cli`` tipado, o ``None`` si el agente no lo declara."""
        bloque = self.channels.get("cli")
        return bloque if isinstance(bloque, CliChannelConfig) else None


# ---------------------------------------------------------------------------
# GlobalConfig — config del sistema (sin agentes)
# ---------------------------------------------------------------------------


class FacesConfig(_ConfigBaseModel):
    """Configuración del proveedor de reconocimiento facial (InsightFace)."""

    provider: Literal["insightface"] = "insightface"
    """Motor de detección y embedding facial. ``insightface`` es el único soportado.

    DECLARATIVO: al haber una sola opción, ningún componente lo lee — el adapter
    de visión se instancia directo. Existe para que agregar un segundo motor no
    sea un breaking change de config."""

    model: Literal["buffalo_sc", "buffalo_s", "buffalo_l"] = "buffalo_sc"
    """Pack de modelos de InsightFace, de más liviano a más preciso.

    ``buffalo_sc`` (default, ~200MB) es el indicado para la Pi 5; ``buffalo_s`` y
    ``buffalo_l`` ganan precisión a costa de RAM y descarga (``buffalo_l`` ~1GB).
    ⚠ Cambiarlo INVALIDA ``faces.db``: los embeddings guardados son de otro
    espacio vectorial. Hay que parar el daemon, borrar la DB y volver a enrolar
    todas las caras."""

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


class SceneConfig(_ConfigBaseModel):
    """Configuración del proveedor de descripción de escena (LLM multimodal)."""

    provider: Literal["anthropic", "openai", "groq"] = "anthropic"
    """Vendor del LLM multimodal que describe la foto: ``anthropic``, ``openai`` o ``groq``.

    Registry PROPIO, independiente del de ``llm``: cada opción tiene su adapter
    de escena. Si ``api_key`` está vacío, la credencial se busca en
    ``providers:`` por esta misma key (o por una entrada cuyo ``type`` coincida).
    Un valor fuera de los tres soportados aborta el arranque del pipeline."""

    model: str = "claude-sonnet-4-6"
    """Modelo multimodal a usar, en el nombre que espera el ``provider`` elegido.

    Tiene que soportar visión: ``claude-sonnet-4-6`` para anthropic, ``gpt-4o``
    para openai, un scout de llama-4 para groq."""

    prompt_template: str | None = None
    """Prompt personalizado en español. None = usar el prompt built-in del adaptador."""
    api_key: str | None = Field(default=None, json_schema_extra={"secret": True})
    """API key del proveedor. Conviene referenciar una entrada de ``providers:`` bajo photos.scene.api_key."""


class DedupConfig(_ConfigBaseModel):
    """Configuración del job nocturno de deduplicación de personas."""

    enabled: bool = True
    """Habilita el job nocturno de deduplicación de personas.

    Con ``False`` la tarea builtin no se registra en el scheduler. Requiere
    además ``photos.enabled: true`` y un scheduler activo — el job vive en el
    daemon."""

    schedule: str = "0 3 * * *"
    """Expresión cron para el job de deduplicación. Validada por croniter."""
    similarity_threshold: float = 0.70
    """Score mínimo de similitud coseno entre centroides para reportar par duplicado."""


class PhotosConfig(_ConfigBaseModel):
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
    """Reconocimiento facial local (InsightFace): qué modelo y con qué umbrales decide."""

    scene: SceneConfig = SceneConfig()
    """Descripción de la escena vía LLM multimodal: vendor, modelo, prompt y credencial."""

    dedup: DedupConfig = DedupConfig()
    """Job nocturno que detecta personas registradas dos veces y reporta los pares."""


class GlobalConfig(_ConfigBaseModel):
    """Config del sistema (``config/global.yaml``) — sin agentes.

    Cumple dos roles que conviene no confundir:

    1. **Base del merge**: los bloques que también existen en ``AgentConfig``
       (``llm``, ``embedding``, ``memories``, ``chat_history``, ``skills``,
       ``tools``, ``semantic_routing``, ``workspace``, ``transcription``) son
       DEFAULTS — cada agente los hereda y pisa solo los campos que declara.
    2. **Config exclusivamente global**: ``app``, ``scheduler``, ``knowledge``,
       ``photos``, ``admin``, ``user``, ``channels``, ``delegation`` y
       ``providers`` no tienen contraparte per-agente. Son recursos del arnés o
       políticas del proceso; escribirlos en ``agents/{id}.yaml`` no los
       override — según el caso se rechaza como clave desconocida o se filtra.

    Las credenciales viven en este mismo fichero (registry ``providers``,
    ``admin.auth_key``), que se crea con permisos 600 y NUNCA se commitea. La
    marca ``secret`` del schema sirve para enmascarar el campo en la TUI, no
    para separarlo en otro archivo.
    """

    app: AppConfig
    """Arranque del proceso: logging, agente por defecto y extensiones. Sin override per-agente."""

    llm: LLMConfig
    """Modelo conversacional por DEFECTO. Cada agente lo hereda y lo pisa campo a campo."""

    embedding: EmbeddingConfig
    """Vectorizador por DEFECTO para routing, memoria y knowledge. Heredable por agente."""

    memories: MemoriesConfig
    """Defaults de la memoria a largo plazo: store, digest y los dos jobs nocturnos.

    Los flags ``enabled`` de consolidación y reconciliación son PER-AGENTE:
    declararlos acá no enciende nada, van en ``agents/{id}.yaml``."""

    chat_history: ChatHistoryConfig
    """Defaults del historial de conversación a corto plazo. Heredable por agente."""

    channels: ChannelsGlobalConfig = ChannelsGlobalConfig()
    """Flags de presentación transversales a todos los canales. Solo global."""
    skills: SkillsConfig = SkillsConfig()
    """Defaults de la selección RAG de skills. Heredable por agente."""

    tools: ToolsConfig = ToolsConfig()
    """Defaults de selección y ejecución de tools. Heredable por agente."""

    semantic_routing: SemanticRoutingConfig = SemanticRoutingConfig()
    """Defaults de las políticas de routing comunes a skills y tools. Heredable por agente."""

    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    """Motor de tareas programadas. Recurso HARNESS-GLOBAL: una sola instancia, sin per-agente.

    Solo corre bajo ``inaki daemon``. Para aislar agendas hay que levantar otra
    instancia del arnés con su propio ``--home`` / ``INAKI_HOME``."""

    # default_factory (no `= SchedulerConfig()`): los campos RuntimePath se resuelven
    # contra `get_inaki_home()` en CADA instanciación de GlobalConfig (runtime, ya con el
    # home seteado), no al importar el módulo. Sin esto, `--home` no relocaliza la db si
    # el bloque `scheduler` falta del YAML. Vale para todo config con RuntimePath usado
    # como default de GlobalConfig/AgentConfig.
    workspace: WorkspaceConfig = WorkspaceConfig()
    """Sandbox de filesystem por DEFECTO para las tools. Heredable por agente."""

    delegation: DelegationConfig = DelegationConfig()
    """Presupuestos de una llamada delegada: iteraciones y timeout. Solo global.

    QUIÉN puede delegar y a quién se decide per-agente
    (``AgentConfig.delegation``); acá van únicamente los límites, iguales para
    todas las delegaciones del arnés."""

    admin: AdminConfig = AdminConfig()
    """Admin server HTTP del daemon: dónde escucha y con qué clave se protege.

    Es la puerta por la que la CLI habla con el daemon y por la que se expone el
    gateway ``POST /admin/tool/invoke``."""

    user: UserConfig = UserConfig()
    """Preferencias del dueño de la instancia (hoy: la timezone del cron y los timestamps)."""

    transcription: TranscriptionConfig | None = None
    """Provider de transcripción de audio por DEFECTO. ``None`` = sin transcripción global.

    Un agente puede declarar el suyo y pisarlo. Si un canal tiene
    ``voice_enabled: true`` y no hay bloque en ninguno de los dos niveles, ese
    agente falla al construirse."""

    knowledge: KnowledgeConfig = Field(
        default_factory=KnowledgeConfig
    )  # default_factory: ver nota en `scheduler` (RuntimePath en T7)
    """Pipeline de RAG sobre fuentes externas (documentos, SQLite) y sus índices.

    Recurso HARNESS-GLOBAL: se declara SOLO acá — no existe ``knowledge`` en
    ``AgentConfig``, así que todos los agentes comparten el mismo corpus y los
    mismos umbrales. Para aislar corpus hay que levantar otra instancia del arnés
    con su propio ``--home`` / ``INAKI_HOME``."""

    photos: PhotosConfig | None = None
    """Configuración del pipeline de fotos. None = feature desactivada (no se carga nada)."""
    providers: dict[str, ProviderConfig] = {}
    """Registry top-level de proveedores — credenciales compartidas por vendor."""
