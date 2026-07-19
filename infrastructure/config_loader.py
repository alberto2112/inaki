"""Carga y merge de configuración de Inaki.

Lee las 4 capas YAML, mergea, valida contra el schema (``config_schema``) y
expone ``load_global_config`` / ``load_agent_config`` / ``AgentRegistry`` +
el bootstrap del directorio del usuario. Importá desde ``infrastructure.config``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterable, Protocol

import yaml

from infrastructure.config_schema import (
    AdminConfig,
    AgentConfig,
    AgentDelegationConfig,
    AppConfig,
    ChannelsGlobalConfig,
    ChatHistoryConfig,
    DedupConfig,
    DelegationConfig,
    EmbeddingConfig,
    FacesConfig,
    GlobalConfig,
    KnowledgeConfig,
    KnowledgeSourceConfig,
    LLMConfig,
    MemoriesConfig,
    PhotosConfig,
    ProviderConfig,
    SceneConfig,
    SchedulerConfig,
    SemanticRoutingConfig,
    SkillsConfig,
    ToolsConfig,
    TranscriptionConfig,
    UserConfig,
    WorkspaceConfig,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Utilidades de merge
# ---------------------------------------------------------------------------


def _deep_merge(base: dict, override: dict) -> dict:
    """
    Merge recursivo campo a campo. Los campos ausentes en override se heredan de base.
    Nunca elimina campos. override tiene prioridad sobre base.
    """
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


SUBAGENT_DEFAULTS: dict = {
    "llm": {"inherit": True},
    "memories": {
        "consolidation": {"enabled": False},
        "reconciliation": {"enabled": False},
    },
    "channels": {},
}
"""
Defaults de rol para sub-agentes (one-shot, sin canales propios).

- `llm.inherit: True` — único bloque heredado por default; se resuelve contra el padre
  (registry build time: contra `global_raw`; flujo delegate: contra el caller, vía T4).
- `memories.consolidation/reconciliation.enabled = False` — los sub-agentes no corren jobs
  que persistan/muten memoria por su cuenta.
- `channels = {}` — sin canales propios (solo invocables por delegación).

Resto de bloques: SIN `inherit` — el YAML del sub-agente opta in por bloque con `inherit: true`.
"""


def resolve_inherit(child_raw: dict, parent_raw: dict) -> dict:
    """
    Resuelve el primitivo `inherit` por bloque top-level antes de validar con pydantic.

    Por cada bloque de `child_raw` que sea un dict con `inherit: True`, el resultado
    es `_deep_merge(parent_raw.get(block, {}), child_block_sin_inherit)` — el bloque
    del padre como base, con los campos del hijo (si los hay) pisando encima. Bloques
    sin `inherit` (o con `inherit: False`) quedan tal cual vinieron en `child_raw`.
    La clave `inherit` siempre se strippea del resultado: no es dato de dominio, así
    que nunca debe llegar a un modelo pydantic.
    """
    result: dict = {}
    for key, value in child_raw.items():
        if isinstance(value, dict) and value.get("inherit") is True:
            child_block = {k: v for k, v in value.items() if k != "inherit"}
            parent_block = parent_raw.get(key, {})
            if not isinstance(parent_block, dict):
                parent_block = {}
            result[key] = _deep_merge(parent_block, child_block)
        elif isinstance(value, dict) and "inherit" in value:
            result[key] = {k: v for k, v in value.items() if k != "inherit"}
        else:
            result[key] = value
    return result


def _load_yaml_safe(path: Path) -> dict:
    """Carga un YAML. Retorna dict vacío si el archivo no existe."""
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


# ---------------------------------------------------------------------------
# Bootstrap del directorio del usuario (~/.inaki)
# ---------------------------------------------------------------------------

_GLOBAL_YAML_HEADER = """\
# =============================================================================
# Inaki — Configuración global
# =============================================================================
#
# Este archivo fue generado automáticamente en el primer arranque con los
# valores por defecto del sistema. Podés editarlo a mano.
#
# Referencia completa de todos los parámetros disponibles:
#   config.example.yaml (en el repo de Inaki)
#
# Layout:
#   ~/.inaki/config/global.yaml          ← este archivo (config base)
#   ~/.inaki/config/global.secrets.yaml  ← secrets (api keys)
#   ~/.inaki/agents/{id}.yaml            ← config de cada agente
#   ~/.inaki/agents/{id}.secrets.yaml    ← secrets por agente (opcional)
# =============================================================================

"""

_SECRETS_YAML_HEADER = """\
# =============================================================================
# Inaki — Secrets globales
# =============================================================================
#
# Poné acá las API keys compartidas entre todos los agentes.
# Este archivo NUNCA debe commitearse a un repositorio.
#
# Las credenciales viven en el bloque top-level `providers:` y se referencian
# desde cada feature (`llm`, `embedding`, `transcription`, `memory.llm`) por
# el campo `provider: <key>`. Esto evita duplicar api_key cuando varias
# features comparten vendor.
#
# Ejemplo:
#
#   providers:
#     openrouter:
#       api_key: "sk-or-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
#     groq:
#       api_key: "gsk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
#       base_url: "https://api.groq.com/openai/v1"
#     openai:
#       api_key: "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
#
# Los secrets por agente (tokens de Telegram, auth_keys REST) van en
# ~/.inaki/agents/{id}.secrets.yaml
# =============================================================================
"""


_DELEGATION_SECTION_COMMENT = """\

# -----------------------------------------------------------------------------
# [delegation] — Delegación agente-a-agente (defaults globales)
# -----------------------------------------------------------------------------
#
# Controla los valores por defecto para la ejecución de sub-agentes delegados.
# Per-agent `delegation.enabled: true` y `allowed_targets: [...]` siguen siendo
# necesarios en cada agents/{id}.yaml para habilitar la delegación en ese agente.
#
# Nota: NO existe campo `max_depth` — la prevención de recursión es estructural
# (el tool `delegate` se filtra automáticamente de los schemas del sub-agente).
#
# delegation:
#   max_iterations_per_sub: 10   # máx. iteraciones del tool-loop por llamada delegada
#   timeout_seconds: 60          # presupuesto de reloj por llamada delegada (asyncio.wait_for)
"""


def _render_default_global_yaml() -> str:
    """Serializa los defaults de las clases Pydantic como YAML con header."""
    defaults = {
        "app": AppConfig().model_dump(),
        "llm": LLMConfig().model_dump(),
        "embedding": EmbeddingConfig().model_dump(),
        "memories": MemoriesConfig().model_dump(),
        "chat_history": ChatHistoryConfig().model_dump(),
        "channels": ChannelsGlobalConfig().model_dump(),
        "skills": SkillsConfig().model_dump(),
        "tools": ToolsConfig().model_dump(),
        "scheduler": SchedulerConfig().model_dump(),
        "workspace": WorkspaceConfig().model_dump(),
        "transcription": TranscriptionConfig().model_dump(),
        "user": UserConfig().model_dump(),
    }
    body = yaml.safe_dump(defaults, sort_keys=False, default_flow_style=False)
    return _GLOBAL_YAML_HEADER + body + _DELEGATION_SECTION_COMMENT


def ensure_user_config(config_dir: Path, agents_dir: Path) -> None:
    """
    Bootstrap idempotente del layout ~/.inaki/.

    Crea `config_dir`, `agents_dir`, `global.yaml` y `global.secrets.yaml`
    si no existen. No toca archivos ya presentes.
    """
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        agents_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.error("No se pudo crear el directorio de configuración: %s", exc)
        raise

    global_yaml = config_dir / "global.yaml"
    if not global_yaml.exists():
        try:
            global_yaml.write_text(_render_default_global_yaml(), encoding="utf-8")
        except OSError as exc:
            logger.error("No se pudo escribir %s: %s", global_yaml, exc)
            raise
        logger.info("Config creada: %s", global_yaml)

    secrets_yaml = config_dir / "global.secrets.yaml"
    if not secrets_yaml.exists():
        try:
            secrets_yaml.write_text(_SECRETS_YAML_HEADER, encoding="utf-8")
        except OSError as exc:
            logger.error("No se pudo escribir %s: %s", secrets_yaml, exc)
            raise
        logger.info("Secrets file creado: %s", secrets_yaml)

    migrate_tool_config_to_own_file(config_dir)
    migrate_telegram_group_fields(config_dir)


def migrate_tool_config_to_own_file(config_dir: Path) -> None:
    """Migración one-shot: mueve el bloque ``tool_config:`` de
    ``global.secrets.yaml`` a su propio ``tool_config.yaml``.

    El store ahora es dueño de su archivo (``tool_config.yaml``) y el operador
    recupera ``global.secrets.yaml`` como archivo de SOLO credenciales que el
    daemon no pisa. Esta función traslada el bloque de instalaciones previas.

    Idempotente: si ``tool_config.yaml`` ya existe, o ``global.secrets.yaml`` no
    tiene el bloque, no hace nada. Orden seguro: escribe el archivo nuevo ANTES
    de limpiar el viejo — en el peor caso quedan duplicados (benigno: el store
    solo lee ``tool_config.yaml``), nunca pérdida de datos. La ``secret.key`` no
    cambia, así que los ``enc:`` siguen descifrándose.
    """
    store_path = config_dir / "tool_config.yaml"
    secrets_path = config_dir / "global.secrets.yaml"

    if store_path.exists() or not secrets_path.exists():
        return

    from ruamel.yaml import YAML

    yaml_rt = YAML()
    yaml_rt.preserve_quotes = True
    try:
        with secrets_path.open("r", encoding="utf-8") as f:
            secrets_doc = yaml_rt.load(f) or {}
    except OSError as exc:
        logger.error("Migración tool_config: no se pudo leer %s (%s)", secrets_path, exc)
        return

    bloque = secrets_doc.get("tool_config")
    if not bloque:
        return  # nada que migrar

    # 1) Escribir el archivo nuevo PRIMERO (datos a salvo antes de limpiar).
    try:
        with store_path.open("w", encoding="utf-8") as f:
            yaml_rt.dump({"tool_config": bloque}, f)
        store_path.chmod(0o600)
    except OSError as exc:
        logger.error(
            "Migración tool_config: no se pudo escribir %s (%s) — abortando, "
            "los datos quedan en global.secrets.yaml",
            store_path,
            exc,
        )
        return

    # 2) Recién ahora, limpiar el bloque de global.secrets.yaml (resto intacto).
    try:
        del secrets_doc["tool_config"]
        with secrets_path.open("w", encoding="utf-8") as f:
            yaml_rt.dump(secrets_doc, f)
    except OSError as exc:
        logger.warning(
            "Migración tool_config: %s creado, pero no se pudo limpiar el bloque "
            "viejo de %s (%s) — duplicado benigno, el store ignora el bloque en secrets",
            store_path,
            secrets_path,
            exc,
        )
        return

    logger.info("Migración tool_config: bloque movido de %s a %s", secrets_path, store_path)


# Campos de *comportamiento en grupos* que migraron de ``channels.telegram.broadcast``
# a ``channels.telegram.groups``. El transporte TCP (port/remote/auth/emit) NO se toca.
_GROUP_BEHAVIOR_FIELDS = ("behavior", "bot_username", "rate_limiter", "rate_limiter_window")


def migrate_telegram_group_fields(config_dir: Path) -> None:
    """Migración one-shot: mueve ``behavior``/``bot_username``/``rate_limiter``/
    ``rate_limiter_window`` de ``channels.telegram.broadcast`` a
    ``channels.telegram.groups``.

    Esos campos describen *cómo responde el bot en un grupo* (aplica con o sin
    broadcast TCP), pero vivían en ``BroadcastConfig``, lo que obligaba a levantar
    el transporte solo para configurarlos. Esta función reubica instalaciones previas.

    Procesa ``global.yaml``, ``global.secrets.yaml`` y todos los YAML bajo
    ``agents/`` — cada campo puede vivir en cualquier capa del merge de 4 niveles.
    Idempotente: si ``broadcast`` no tiene ninguno de los campos, no toca el archivo.
    ``groups`` gana ante conflicto (campo presente en ambos → se descarta el de
    ``broadcast``). Si ``broadcast`` queda vacío tras mover (solo tenía comportamiento,
    sin transporte) se elimina el bloque. Preserva comentarios (ruamel).
    """
    from ruamel.yaml import YAML

    yaml_rt = YAML()
    yaml_rt.preserve_quotes = True

    archivos = [config_dir / "global.yaml", config_dir / "global.secrets.yaml"]
    agents_dir = config_dir / "agents"
    if agents_dir.is_dir():
        archivos.extend(sorted(agents_dir.glob("*.yaml")))

    for path in archivos:
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8") as f:
                doc = yaml_rt.load(f)
        except OSError as exc:
            logger.error("Migración groups: no se pudo leer %s (%s)", path, exc)
            continue
        if not isinstance(doc, dict) or not _move_group_fields_broadcast_to_groups(doc):
            continue
        try:
            with path.open("w", encoding="utf-8") as f:
                yaml_rt.dump(doc, f)
        except OSError as exc:
            logger.error("Migración groups: no se pudo escribir %s (%s)", path, exc)
            continue
        logger.info("Migración groups: comportamiento movido broadcast→groups en %s", path)


def _move_group_fields_broadcast_to_groups(doc: dict) -> bool:
    """Mueve los campos de comportamiento de ``telegram.broadcast`` a
    ``telegram.groups`` dentro de un doc ruamel ya cargado. Devuelve ``True`` si
    hubo cambios (in-place sobre ``doc``)."""
    channels = doc.get("channels")
    if not isinstance(channels, dict):
        return False
    telegram = channels.get("telegram")
    if not isinstance(telegram, dict):
        return False
    broadcast = telegram.get("broadcast")
    if not isinstance(broadcast, dict):
        return False

    presentes = [campo for campo in _GROUP_BEHAVIOR_FIELDS if campo in broadcast]
    if not presentes:
        return False

    groups = telegram.get("groups")
    if not isinstance(groups, dict):
        groups = {}
        telegram["groups"] = groups

    for campo in presentes:
        valor = broadcast.pop(campo)
        # groups gana ante conflicto: solo escribimos si no estaba ya definido ahí.
        if campo not in groups:
            groups[campo] = valor

    # Un broadcast sin transporte (port/remote) ya no es broadcast: lo eliminamos
    # para no disparar el validador port-XOR-remote con un bloque vacío.
    if not broadcast:
        del telegram["broadcast"]

    return True


class _HasChannels(Protocol):
    """Subset estructural de ``AgentConfig`` que ``ensure_user_channel_dirs``
    necesita. Declarado como Protocol para que tests puedan pasar stubs sin
    construir un ``AgentConfig`` completo."""

    channels: dict[str, dict[str, Any]]


def ensure_user_channel_dirs(inaki_home: Path, agent_configs: Iterable[_HasChannels]) -> None:
    """Crea ``<inaki_home>/users/{channel}/`` por cada canal configurado en cualquier agente.

    Soporta la convención de archivos per-user (ver ``RunAgentUseCase._read_user_context``).
    El operador no tiene que hacer ``mkdir`` manual: la primera vez que un agente
    declara, por ejemplo, ``channels.telegram``, se crea ``~/.inaki/users/telegram/``
    vacío. La discoverability sale gratis: ``ls ~/.inaki/users/`` muestra dónde van
    los archivos.

    Idempotente — se ejecuta en cada arranque del daemon (y en reloads). Errores
    de permisos no abortan el arranque: log warning y seguir. Si el canal no
    tiene humanos detrás (ej. broadcast interno) igual se crea el dir; aceptable
    porque el costo es nulo y evita lógica de "qué canal merece subdir".
    """
    base = inaki_home / "users"
    canales: set[str] = set()
    for cfg in agent_configs:
        canales.update(cfg.channels.keys())

    for canal in sorted(canales):
        path = base / canal
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("No se pudo crear %s: %s", path, exc)


# ---------------------------------------------------------------------------
# Legacy shape detection
# ---------------------------------------------------------------------------


_LEGACY_FIELDS: tuple[tuple[str, str], ...] = (
    ("llm", "api_key"),
    ("llm", "base_url"),
    ("embedding", "api_key"),
    ("embedding", "base_url"),
    ("transcription", "api_key"),
    ("transcription", "base_url"),
)


_LEGACY_ERROR_TEMPLATE = """\
Formato legacy detectado en config: '{field}' ya no existe. \
Las credenciales ahora viven en el bloque top-level 'providers:'. Ejemplo:

  providers:
    groq: {{ api_key: TU_API_KEY, base_url: https://api.groq.com/openai/v1 }}
  llm:
    provider: groq
    model: gpt-oss-120b

Ver docs/configuracion.md#providers.\
"""


def _check_legacy_shape(merged: dict) -> None:
    """
    Inspecciona el dict crudo mergeado y rechaza el shape viejo.

    Busca ``llm.api_key``, ``llm.base_url``, ``embedding.{api_key,base_url}``,
    ``transcription.{api_key,base_url}``, ``memory.llm.{api_key,base_url}``.
    Si alguno existe levanta ``ConfigError`` con mensaje accionable en español
    que incluye un ejemplo del shape nuevo.

    DEBE correr ANTES de ``model_validate`` porque pydantic strict rechazaría
    el field desconocido con un mensaje genérico, perdiendo el ejemplo.
    """
    from core.domain.errors import ConfigError

    for section, key in _LEGACY_FIELDS:
        node = merged.get(section)
        if isinstance(node, dict) and key in node:
            raise ConfigError(_LEGACY_ERROR_TEMPLATE.format(field=f"{section}.{key}"))

    memories = merged.get("memories")
    if isinstance(memories, dict):
        memories_llm = memories.get("llm")
        if isinstance(memories_llm, dict):
            for key in ("api_key", "base_url"):
                if key in memories_llm:
                    raise ConfigError(_LEGACY_ERROR_TEMPLATE.format(field=f"memories.llm.{key}"))


def _parse_providers(merged: dict) -> dict[str, ProviderConfig]:
    """Construye el dict ``{key: ProviderConfig}`` desde el merged raw."""
    providers_raw = merged.get("providers") or {}
    if not isinstance(providers_raw, dict):
        from core.domain.errors import ConfigError

        raise ConfigError("El bloque 'providers:' debe ser un diccionario de entradas por vendor.")
    return {key: ProviderConfig(**(entry or {})) for key, entry in providers_raw.items()}


# ---------------------------------------------------------------------------
# Carga de configuración
# ---------------------------------------------------------------------------


def load_global_config(config_dir: Path) -> tuple[GlobalConfig, dict]:
    """
    Carga y mergea global.yaml + global.secrets.yaml.
    Retorna (GlobalConfig, raw_dict) — el dict raw se usa para merge con agentes.
    """
    base = _load_yaml_safe(config_dir / "global.yaml")
    secrets = _load_yaml_safe(config_dir / "global.secrets.yaml")

    if not secrets and not (config_dir / "global.secrets.yaml").exists():
        logger.debug("global.secrets.yaml no encontrado — usando solo global.yaml")

    merged = _deep_merge(base, secrets)

    _check_legacy_shape(merged)
    providers = _parse_providers(merged)

    app = AppConfig(**merged.get("app", {}))
    llm = LLMConfig(**merged.get("llm", {}))
    embedding = EmbeddingConfig(**merged.get("embedding", {}))
    memories = MemoriesConfig(**merged.get("memories", {}))
    chat_history = ChatHistoryConfig(**merged.get("chat_history", {}))

    skills = SkillsConfig(**merged.get("skills", {}))
    tools = ToolsConfig(**merged.get("tools", {}))
    semantic_routing = SemanticRoutingConfig(**merged.get("semantic_routing", {}))
    scheduler = SchedulerConfig(**merged.get("scheduler", {}))
    workspace = WorkspaceConfig(**merged.get("workspace", {}))
    delegation = DelegationConfig(**merged.get("delegation", {}))
    admin = AdminConfig(**merged.get("admin", {}))
    user = UserConfig(**merged.get("user", {}))
    transcription = (
        TranscriptionConfig(**merged["transcription"])
        if merged.get("transcription") is not None
        else None
    )

    knowledge_raw = merged.get("knowledge")
    if knowledge_raw is not None:
        sources_raw = knowledge_raw.pop("sources", []) or []
        sources = [KnowledgeSourceConfig(**s) for s in sources_raw]
        knowledge = KnowledgeConfig(**knowledge_raw, sources=sources)
    else:
        knowledge = KnowledgeConfig()

    photos_raw = merged.get("photos")
    if photos_raw is not None:
        faces_raw = photos_raw.pop("faces", {}) or {}
        scene_raw = photos_raw.pop("scene", {}) or {}
        dedup_raw = photos_raw.pop("dedup", {}) or {}
        photos = PhotosConfig(
            **photos_raw,
            faces=FacesConfig(**faces_raw),
            scene=SceneConfig(**scene_raw),
            dedup=DedupConfig(**dedup_raw),
        )
    else:
        photos = None

    global_cfg = GlobalConfig(
        app=app,
        llm=llm,
        embedding=embedding,
        memories=memories,
        chat_history=chat_history,
        skills=skills,
        tools=tools,
        semantic_routing=semantic_routing,
        scheduler=scheduler,
        workspace=workspace,
        delegation=delegation,
        admin=admin,
        user=user,
        transcription=transcription,
        knowledge=knowledge,
        photos=photos,
        providers=providers,
    )
    return global_cfg, merged


def _filter_channel_adapters(raw: dict) -> dict:
    """Filtra el campo ``channels`` heredado del global para excluir flags transversales.

    ``GlobalConfig.channels`` (``ChannelsGlobalConfig``) y ``AgentConfig.channels``
    (``dict[str, dict[str, Any]]``) comparten clave de YAML pero significan cosas
    distintas. El deep-merge propaga los flags globales al merged del agente; este
    filtro deja solo los valores que son dicts (los adapters como ``telegram``,
    ``cli``, ``broadcast`` per-grupo, etc.) y descarta scalars.
    """
    return {k: v for k, v in raw.items() if isinstance(v, dict)}


def assemble_agent_config(merged: dict) -> AgentConfig:
    """Ensambla un ``AgentConfig`` desde un dict YA mergeado y resuelto.

    Asume que ``merged`` pasó por los merges de capas (``load_agent_config``) o por
    el builder efímero del flujo delegate (``AgentContainer.build_ephemeral_child``)
    y por la resolución de ``inherit``. Es el ÚNICO punto donde el mapeo
    dict → ``AgentConfig`` vive: lo comparten ambos callers.

    Lanza ``KeyError`` si falta un campo requerido (``id``/``name``/``description``)
    o ``ValueError`` si un sub-modelo es inválido. El caller decide la política:
    ``load_agent_config`` envuelve en try/except → ``None``; el builder efímero propaga.
    """
    providers = _parse_providers(merged)
    transcription_raw = merged.get("transcription")
    transcription = (
        TranscriptionConfig(**transcription_raw) if transcription_raw is not None else None
    )
    return AgentConfig(
        id=merged["id"],
        name=merged["name"],
        description=merged["description"],
        system_prompt=merged.get("system_prompt", ""),
        llm=LLMConfig(**merged.get("llm", {})),
        embedding=EmbeddingConfig(**merged.get("embedding", {})),
        memories=MemoriesConfig(**merged.get("memories", {})),
        chat_history=ChatHistoryConfig(**merged.get("chat_history", {})),
        skills=SkillsConfig(**merged.get("skills", {})),
        tools=ToolsConfig(**merged.get("tools", {})),
        semantic_routing=SemanticRoutingConfig(**merged.get("semantic_routing", {})),
        workspace=WorkspaceConfig(**merged.get("workspace", {})),
        delegation=AgentDelegationConfig(**merged.get("delegation", {})),
        transcription=transcription,
        channels=_filter_channel_adapters(merged.get("channels", {})),
        providers=providers,
    )


def load_agent_config(
    agent_id: str,
    agents_dir: Path,
    global_raw: dict,
    extra_base: dict | None = None,
) -> AgentConfig | None:
    """
    Carga y mergea la config de un agente:
      global_raw → extra_base → agents/{id}.yaml → agents/{id}.secrets.yaml

    ``extra_base`` son los defaults de rol (ej. sub-agentes, ver ``SUBAGENT_DEFAULTS``):
    pisan a ``global_raw`` pero el YAML explícito del agente sigue pisando por encima.

    Retorna None si el agente tiene config inválida (loggea WARNING).
    """
    agent_yaml = agents_dir / f"{agent_id}.yaml"
    agent_secrets = agents_dir / f"{agent_id}.secrets.yaml"

    if not agent_yaml.exists():
        logger.warning("Config del agente '%s' no encontrada: %s", agent_id, agent_yaml)
        return None

    agent_raw = _load_yaml_safe(agent_yaml)

    if agent_secrets.exists():
        secrets_raw = _load_yaml_safe(agent_secrets)
        agent_raw = _deep_merge(agent_raw, secrets_raw)
    else:
        logger.warning(
            "Agente '%s': %s no encontrado — canales con secrets no levantarán.",
            agent_id,
            agent_secrets.name,
        )

    if extra_base is not None:
        global_raw = _deep_merge(global_raw, extra_base)

    # Merge: global como base, agente como override
    merged = _deep_merge(global_raw, agent_raw)

    _check_legacy_shape(merged)

    try:
        return assemble_agent_config(merged)
    except (KeyError, ValueError) as exc:
        logger.warning("Config inválida para agente '%s': %s", agent_id, exc)
        return None


# ---------------------------------------------------------------------------
# AgentRegistry
# ---------------------------------------------------------------------------


class AgentRegistry:
    """
    Escanea el directorio de agentes al arrancar y construye el registro.

    - ``agents_dir/*.yaml``             → agentes regulares (instanciados al inicio, con canales)
    - ``agents_dir/sub-agents/*.yaml``  → sub-agentes (solo para delegación, sin canales)

    Los agentes con config inválida se omiten con WARNING.
    """

    def __init__(self, agents_dir: Path, global_raw: dict) -> None:
        self._agents: dict[str, AgentConfig] = {}
        self._sub_agent_ids: set[str] = set()
        self._sub_agent_raw: dict[str, dict] = {}

        if not agents_dir.exists():
            logger.warning("Directorio de agentes no encontrado: %s", agents_dir)
            return

        for yaml_file in sorted(agents_dir.glob("*.yaml")):
            if ".secrets" in yaml_file.name or ".example" in yaml_file.name:
                continue
            agent_id = yaml_file.stem
            cfg = load_agent_config(agent_id, agents_dir, global_raw)
            if cfg is not None:
                self._agents[agent_id] = cfg
                logger.debug("Agente '%s' cargado: %s", agent_id, cfg.name)

        sub_agents_dir = agents_dir / "sub-agents"
        if sub_agents_dir.exists():
            for yaml_file in sorted(sub_agents_dir.glob("*.yaml")):
                if ".secrets" in yaml_file.name or ".example" in yaml_file.name:
                    continue
                agent_id = yaml_file.stem

                # Defaults de rol (SUBAGENT_DEFAULTS) inyectados como capa de prioridad
                # más baja: el YAML del sub-agente (explícito) y global_raw siguen
                # pisando por encima — ver `load_agent_config(extra_base=...)`.
                extra_base = resolve_inherit(SUBAGENT_DEFAULTS, global_raw)
                cfg = load_agent_config(agent_id, sub_agents_dir, global_raw, extra_base=extra_base)
                if cfg is not None:
                    self._agents[agent_id] = cfg
                    self._sub_agent_ids.add(agent_id)
                    self._sub_agent_raw[agent_id] = self._load_sub_agent_raw_delta(
                        agent_id, sub_agents_dir
                    )
                    logger.debug("Sub-agente '%s' cargado: %s", agent_id, cfg.name)

        regular_count = len(self._agents) - len(self._sub_agent_ids)
        logger.info(
            "AgentRegistry: %d agente(s) + %d sub-agente(s) cargado(s): %s",
            regular_count,
            len(self._sub_agent_ids),
            list(self._agents),
        )

        regular_agents = {k: v for k, v in self._agents.items() if k not in self._sub_agent_ids}
        _validate_channel_uniqueness(regular_agents)

    def get(self, agent_id: str) -> AgentConfig:
        if agent_id not in self._agents:
            from core.domain.errors import AgentNotFoundError

            raise AgentNotFoundError(
                f"Agente '{agent_id}' no encontrado. Disponibles: {list(self._agents)}"
            )
        return self._agents[agent_id]

    def is_sub_agent(self, agent_id: str) -> bool:
        return agent_id in self._sub_agent_ids

    def list_all(self) -> list[AgentConfig]:
        return list(self._agents.values())

    def list_regular(self) -> list[AgentConfig]:
        return [cfg for id, cfg in self._agents.items() if id not in self._sub_agent_ids]

    def list_sub_agents(self) -> list[AgentConfig]:
        return [cfg for id, cfg in self._agents.items() if id in self._sub_agent_ids]

    def get_sub_agent_raw(self, agent_id: str) -> dict | None:
        """
        Delta crudo (YAML + secrets mergeados, SIN global_raw ni SUBAGENT_DEFAULTS) de un
        sub-agente. Lo usa el builder efímero (`build_ephemeral_child`) para resolver
        `inherit` contra el caller en tiempo de delegación — no contra `global_raw`.
        """
        return self._sub_agent_raw.get(agent_id)

    @staticmethod
    def _load_sub_agent_raw_delta(agent_id: str, sub_agents_dir: Path) -> dict:
        raw = _load_yaml_safe(sub_agents_dir / f"{agent_id}.yaml")
        secrets_path = sub_agents_dir / f"{agent_id}.secrets.yaml"
        if secrets_path.exists():
            raw = _deep_merge(raw, _load_yaml_safe(secrets_path))
        return raw

    def agents_with_channel(self, channel_type: str) -> list[AgentConfig]:
        return [
            a
            for id, a in self._agents.items()
            if id not in self._sub_agent_ids and channel_type in a.channels
        ]


def _validate_channel_uniqueness(agents: dict[str, AgentConfig]) -> None:
    """
    Rechaza configs donde varios agentes comparten la misma identidad de canal,
    o donde un mismo agente tiene dos canales con el mismo ``broadcast.server.port``.

    Motivo: un bot de Telegram solo admite UN ``getUpdates`` activo por token
    (Telegram API). Si dos agentes declaran el mismo token, el daemon levanta
    pollings que se pisan → errores ``Conflict`` en loop.

    El modelo canónico: un solo agente expone el canal (entry point) y delega
    a los subagentes vía la tool ``delegate``. Los subagentes NO deben
    declarar ``channels.telegram`` apuntando al mismo token que el principal.

    Broadcast port uniqueness: dentro de un mismo agente, dos canales no pueden
    declarar el mismo ``broadcast.server.port`` — ambos intentarían hacer
    ``bind()`` en el mismo puerto del host.
    """
    from core.domain.errors import ConfigError

    telegram_tokens: dict[str, list[str]] = {}

    for agent_id, cfg in agents.items():
        tg_cfg = cfg.channels.get("telegram") or {}
        token = tg_cfg.get("token")
        if token:
            telegram_tokens.setdefault(token, []).append(agent_id)

        # Unicidad de broadcast.server.port dentro del mismo agente. Solo los
        # servers hacen bind(); un bloque con enabled=false no levanta transporte.
        broadcast_ports: dict[int, list[str]] = {}
        for channel_name, channel_raw in cfg.channels.items():
            if not isinstance(channel_raw, dict):
                continue
            bc_raw = channel_raw.get("broadcast")
            if not isinstance(bc_raw, dict) or bc_raw.get("enabled") is False:
                continue
            server_raw = bc_raw.get("server")
            if not isinstance(server_raw, dict):
                continue
            bc_port = server_raw.get("port")
            if bc_port is not None:
                broadcast_ports.setdefault(int(bc_port), []).append(channel_name)

        duplicated_bc_ports = {p: chs for p, chs in broadcast_ports.items() if len(chs) > 1}
        if duplicated_bc_ports:
            conflicts = "; ".join(
                f"port {p} declarado en [{', '.join(chs)}]"
                for p, chs in duplicated_bc_ports.items()
            )
            raise ConfigError(
                f"Agente '{agent_id}': broadcast.server.port duplicado — {conflicts}. "
                "Cada canal del agente debe usar un puerto de broadcast distinto."
            )

    duplicated_tokens = {tok: ids for tok, ids in telegram_tokens.items() if len(ids) > 1}

    if duplicated_tokens:
        agent_lists = "; ".join(f"agentes [{', '.join(ids)}]" for ids in duplicated_tokens.values())
        raise ConfigError(
            f"Token de Telegram duplicado entre {agent_lists}. "
            "Un token solo admite un polling activo: dejá 'channels.telegram' únicamente "
            "en el agente que actúa como entry point; los subagentes reciben mensajes "
            "vía la tool 'delegate'."
        )
