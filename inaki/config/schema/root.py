"""Raíces del schema: ``AgentConfig`` y ``GlobalConfig``.

Sección del schema de configuración. Solo declaraciones: sin I/O ni carga de YAML.
Importá desde ``inaki.config.schema`` (o ``inaki.config``).
"""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel, Field, ValidationError, field_validator

from inaki.config.channels import canal_registrado, canales_registrados
from inaki.config.schema._base import _ConfigBaseModel
from inaki.config.schema.admin import AdminConfig
from inaki.config.schema.app import AppConfig
from inaki.config.schema.channels import ChannelsGlobalConfig
from inaki.config.schema.chat_history import ChatHistoryConfig
from inaki.config.schema.delegation import AgentDelegationConfig, DelegationConfig
from inaki.config.schema.embedding import EmbeddingConfig
from inaki.config.schema.knowledge import KnowledgeConfig
from inaki.config.schema.llm import LLMConfig
from inaki.config.schema.memories import MemoriesConfig
from inaki.config.schema.photos import PhotosConfig
from inaki.config.schema.providers import ProviderConfig
from inaki.config.schema.scheduler import SchedulerConfig
from inaki.config.schema.skills import SkillsConfig
from inaki.config.schema.tools import SemanticRoutingConfig, ToolsConfig
from inaki.config.schema.transcription import TranscriptionConfig
from inaki.config.schema.user import UserConfig
from inaki.config.schema.workspace import WorkspaceConfig

_ModeloCanal = TypeVar("_ModeloCanal", bound=BaseModel)


# ---------------------------------------------------------------------------
# AgentConfig — config completa y resuelta para un agente
# ---------------------------------------------------------------------------


class AgentConfig(_ConfigBaseModel):
    """Config completa y RESUELTA de un agente — el resultado del merge, no un fichero.

    Lo que el operador escribe en ``agents/{id}.yaml`` es un DELTA: cada bloque
    que declara pisa campo a campo al homónimo de ``global.yaml``, y lo que no
    menciona se hereda. Este modelo es lo que queda después de ese merge, y es lo
    único que ve el ensamblador al construir el agente.

    Solo ``id``, ``name`` y ``description`` son obligatorios y exclusivos del
    agente: no tienen contraparte global de la que heredar.

    Acá viven únicamente los recursos del tier PER-AGENTE (``llm``, ``embedding``,
    ``memories``, ``chat_history``, ``channels``). Los harness-global
    (``scheduler``, ``knowledge``, ``photos``) no tienen campo en este modelo a
    propósito: declararlos en el YAML de un agente es un error de clave, no un
    override silencioso.

    Los use cases NO reciben este objeto: el composition root lo traduce a Settings
    VOs (``inaki/kernel/domain/agent_settings.py``) para que el dominio no
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

    Los bloques de los canales REGISTRADOS (``inaki.config.channels``) llegan acá
    **ya validados y coercionados a su modelo Pydantic** por ``_validar_channels``.
    Para acceso tipado usá ``canal(nombre, Modelo)``; el dict directo sirve para
    iterar o preguntar qué canales declaró el agente.
    """
    providers: dict[str, ProviderConfig] = {}
    """Registry de proveedores post-merge. Heredado del global + overrides del agente."""

    @field_validator("channels", mode="before")
    @classmethod
    def _validar_channels(cls, value: Any) -> Any:
        """Valida cada bloque de canal contra el modelo que registró su canal.

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
            registrado = canal_registrado(nombre)
            if registrado is None:
                conocidos = ", ".join(canales_registrados()) or "(ninguno registrado)"
                raise ValueError(
                    f"channels.{nombre}: canal desconocido. Canales soportados: {conocidos}."
                )
            schema = registrado.modelo
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

    def canal(self, nombre: str, modelo: type[_ModeloCanal]) -> _ModeloCanal | None:
        """Bloque ``channels.<nombre>`` tipado como ``modelo``, o ``None`` si no lo declara.

        El schema raíz no tiene un campo por canal: los canales se registran desde
        fuera (``inaki.config.channels``) y cada uno expone su accesor
        (``inaki.channels.telegram.config.telegram_config``) sobre este método.
        """
        bloque = self.channels.get(nombre)
        return bloque if isinstance(bloque, modelo) else None


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
    marca ``secret`` del schema sirve para redactar el campo al mostrarlo
    (``inaki config show``), no para separarlo en otro archivo.
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
