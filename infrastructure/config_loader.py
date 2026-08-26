"""Carga y merge de configuración de Inaki.

Lee las 2 capas YAML (``config/global.yaml`` → ``agents/{id}.yaml``), mergea,
valida contra el schema (``config_schema``) y expone ``load_global_config`` /
``load_agent_config`` / ``AgentRegistry`` + el bootstrap del directorio del
usuario. Importá desde ``infrastructure.config``.

Las credenciales viven en esas mismas capas (los ficheros se protegen con
permisos ``600``). Los ``*.secrets.yaml`` fueron erradicados: ver
``migrate_secrets_into_main_layers``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterable, Protocol

import yaml

from core.domain.config_merge import deep_merge, resolver_inherit
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


# El merge y la herencia los define UN solo motor (``core/domain/config_merge``).
# Estos alias preservan los nombres históricos que importan ``container.py``, la
# fachada ``infrastructure/config.py`` y los tests.
_deep_merge = deep_merge
resolve_inherit = resolver_inherit


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
#   config/global.example.yaml (en el repo) — autogenerado desde el schema
#   docs/config-reference.md   (ídem)
#   inaki config show --origin  — la config EFECTIVA de esta instancia
#
# Layout:
#   ~/.inaki/config/global.yaml  ← este archivo (config base + credenciales)
#   ~/.inaki/agents/{id}.yaml    ← config de cada agente (+ sus credenciales)
#
# Las credenciales van en el bloque top-level `providers:` y se referencian
# desde cada feature (`llm`, `embedding`, `transcription`, `memories.llm`) por
# el campo `provider: <key>`. Esto evita duplicar api_key cuando varias
# features comparten vendor. Ejemplo:
#
#   providers:
#     openrouter:
#       api_key: "sk-or-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
#     groq:
#       api_key: "gsk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
#       base_url: "https://api.groq.com/openai/v1"
#
# Este archivo contiene credenciales: se crea con permisos 600 y NUNCA debe
# commitearse a un repositorio.
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

    Crea `config_dir`, `agents_dir` y `global.yaml` si no existen (el archivo
    con permisos 600: contiene credenciales). No toca archivos ya presentes.
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
            global_yaml.chmod(0o600)
        except OSError as exc:
            logger.error("No se pudo escribir %s: %s", global_yaml, exc)
            raise
        logger.info("Config creada: %s", global_yaml)

    # El orden importa: la extracción de `tool_config` lee `global.secrets.yaml`,
    # así que tiene que correr ANTES de que el fold lo haga desaparecer.
    migrate_tool_config_to_own_file(config_dir)
    migrate_telegram_group_fields(config_dir, agents_dir)
    migrate_secrets_into_main_layers(config_dir, agents_dir)


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


def migrate_telegram_group_fields(config_dir: Path, agents_dir: Path) -> None:
    """Migración one-shot: mueve ``behavior``/``bot_username``/``rate_limiter``/
    ``rate_limiter_window`` de ``channels.telegram.broadcast`` a
    ``channels.telegram.groups``.

    Esos campos describen *cómo responde el bot en un grupo* (aplica con o sin
    broadcast TCP), pero vivían en ``BroadcastConfig``, lo que obligaba a levantar
    el transporte solo para configurarlos. Esta función reubica instalaciones previas.

    Procesa ``global.yaml``, ``global.secrets.yaml`` (si sobrevive, corre antes
    del fold) y todos los YAML de ``agents_dir`` y su ``sub-agents/`` — cada
    campo puede vivir en cualquier capa.

    ``agents_dir`` llega como parámetro porque el layout REAL lo tiene como
    sibling de ``config/`` (``~/.inaki/agents/``), no como subcarpeta. La
    versión original lo derivaba como ``config_dir / "agents"`` — el layout de
    los tests — así que en instalaciones reales los ficheros de agente NUNCA se
    migraban. Con los campos viejos ignorándose en silencio nadie lo notó;
    desde que la config falla ruidoso (`config-falla-ruidoso`), un agente sin
    migrar aborta el arranque, y este bug pasó de invisible a fatal.
    Idempotente: si ``broadcast`` no tiene ninguno de los campos, no toca el archivo.
    ``groups`` gana ante conflicto (campo presente en ambos → se descarta el de
    ``broadcast``). Si ``broadcast`` queda vacío tras mover (solo tenía comportamiento,
    sin transporte) se elimina el bloque. Preserva comentarios (ruamel).
    """
    from ruamel.yaml import YAML

    yaml_rt = YAML()
    yaml_rt.preserve_quotes = True

    archivos = [config_dir / "global.yaml", config_dir / "global.secrets.yaml"]
    for directorio in (agents_dir, agents_dir / "sub-agents"):
        if directorio.is_dir():
            archivos.extend(sorted(directorio.glob("*.yaml")))

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


def migrate_secrets_into_main_layers(config_dir: Path, agents_dir: Path) -> None:
    """Migración one-shot: pliega cada ``*.secrets.yaml`` dentro de su capa principal.

    Los ``*.secrets.yaml`` nunca estuvieron cifrados — eran YAML plano con
    permisos 600, igual que su capa principal. Su única ventaja real era poder
    compartir/commitear la config sin credenciales, caso que nadie usa. A cambio
    duplicaban el número de ficheros, de capas del merge y de decisiones
    ("¿dónde escribo este campo?") en toda superficie de edición.

    Tras esta migración las capas son dos: ``config/global.yaml`` y
    ``agents/{id}.yaml`` (más ``agents/sub-agents/{id}.yaml``). La marca de
    "esto es secreto" NO desaparece: vive en el schema (``kind == "secret"``),
    que es lo que usa la TUI para enmascarar el valor al mostrarlo.

    El contenido del secrets PISA al de la capa principal — mismo orden de
    precedencia que tenía el merge que se elimina. Idempotente: si no hay
    ``*.secrets.yaml``, no hace nada. Orden seguro: escribe la capa principal
    ANTES de borrar el secrets — en el peor caso quedan duplicados (benigno,
    el loader ya no lee secrets), nunca pérdida de datos.

    Los comentarios de la capa principal se preservan (ruamel); los del secrets
    se pierden al plegarse, salvo los que cuelgan de un bloque que se copia entero.
    """
    from ruamel.yaml import YAML

    yaml_rt = YAML()
    yaml_rt.preserve_quotes = True
    yaml_rt.width = 4096

    pares: list[tuple[Path, Path]] = [
        (config_dir / "global.secrets.yaml", config_dir / "global.yaml")
    ]
    for directorio in (agents_dir, agents_dir / "sub-agents"):
        if not directorio.is_dir():
            continue
        for secrets_path in sorted(directorio.glob("*.secrets.yaml")):
            principal = secrets_path.with_name(secrets_path.name.replace(".secrets.yaml", ".yaml"))
            if not principal.exists():
                # Secrets HUÉRFANO: su agente fue borrado (la TUI vieja eliminaba
                # solo el YAML principal). Plegarlo fundaría un agente sin `id`
                # que abortaría el arranque. Se deja en disco —el loader ya no lo
                # lee— y se avisa: borrarlo o recrear el agente es del operador.
                logger.warning(
                    "Migración secrets: %s no tiene su %s — es el resto de un agente "
                    "borrado. No se pliega (fundaría un agente inválido). Borralo si "
                    "ya no lo necesitás, o recreá el agente para conservar sus "
                    "credenciales.",
                    secrets_path,
                    principal.name,
                )
                continue
            pares.append((secrets_path, principal))

    tool_config_ya_migrado = (config_dir / "tool_config.yaml").exists()

    for secrets_path, principal_path in pares:
        if not secrets_path.exists():
            continue
        try:
            with secrets_path.open("r", encoding="utf-8") as f:
                secrets_doc = yaml_rt.load(f)
        except OSError as exc:
            logger.error("Migración secrets: no se pudo leer %s (%s)", secrets_path, exc)
            continue

        if not isinstance(secrets_doc, dict):
            secrets_doc = {}

        # El bloque `tool_config` NUNCA se pliega a la capa principal: el store
        # no la lee, y desde el chequeo top-level una clave `tool_config:` en
        # global.yaml abortaría el arranque. Si `tool_config.yaml` ya existe,
        # esta copia es el duplicado benigno de su migración y se descarta; si
        # NO existe, esa migración falló al escribir y los datos se quedan en
        # el secrets (reescrito solo con este bloque) hasta que el operador
        # resuelva — perder credenciales de tools por un OSError no es opción.
        bloque_varado = None
        if "tool_config" in secrets_doc:
            bloque = secrets_doc["tool_config"]
            del secrets_doc["tool_config"]
            if not tool_config_ya_migrado:
                bloque_varado = bloque

        if secrets_doc:
            try:
                with principal_path.open("r", encoding="utf-8") as f:
                    principal_doc = yaml_rt.load(f)
            except FileNotFoundError:
                principal_doc = None
            except OSError as exc:
                logger.error(
                    "Migración secrets: no se pudo leer %s (%s) — %s queda intacto",
                    principal_path,
                    exc,
                    secrets_path.name,
                )
                continue
            if not isinstance(principal_doc, dict):
                principal_doc = {}

            _plegar_en(principal_doc, secrets_doc)

            try:
                with principal_path.open("w", encoding="utf-8") as f:
                    yaml_rt.dump(principal_doc, f)
                principal_path.chmod(0o600)
            except OSError as exc:
                logger.error(
                    "Migración secrets: no se pudo escribir %s (%s) — abortando, "
                    "los datos quedan en %s",
                    principal_path,
                    exc,
                    secrets_path.name,
                )
                continue

        if bloque_varado is not None:
            try:
                with secrets_path.open("w", encoding="utf-8") as f:
                    yaml_rt.dump({"tool_config": bloque_varado}, f)
                logger.warning(
                    "Migración secrets: la migración de tool_config no pudo escribir su "
                    "archivo propio, así que %s se conserva SOLO con ese bloque. Revisá "
                    "permisos/disco y reiniciá para que la migración lo reintente.",
                    secrets_path,
                )
            except OSError as exc:
                logger.error(
                    "Migración secrets: no se pudo reescribir %s (%s) — el fichero queda "
                    "como estaba.",
                    secrets_path,
                    exc,
                )
            continue

        # Recién ahora, con los datos ya en la capa principal, borrar el secrets.
        try:
            secrets_path.unlink()
        except OSError as exc:
            logger.warning(
                "Migración secrets: %s actualizado, pero no se pudo borrar %s (%s) — "
                "duplicado benigno, el loader ya no lee ese archivo",
                principal_path,
                secrets_path,
                exc,
            )
            continue

        logger.info(
            "Migración secrets: %s plegado en %s (permisos 600)", secrets_path, principal_path
        )


def _plegar_en(destino: dict, origen: dict) -> None:
    """Deep merge in-place de ``origen`` sobre ``destino`` (``origen`` pisa).

    Muta ``destino`` en vez de construir un dict nuevo para no perder los
    comentarios que ruamel tiene colgados de la estructura original.
    """
    for key, value in origen.items():
        actual = destino.get(key)
        if isinstance(actual, dict) and isinstance(value, dict):
            _plegar_en(actual, value)
        else:
            destino[key] = value


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


def _check_top_level(
    raw: dict, modelo: type[AgentConfig] | type[GlobalConfig], contexto: str
) -> None:
    """Rechaza claves top-level que el schema no declara.

    El ``extra="forbid"`` de los modelos nunca ve este nivel: el loader arma
    ``GlobalConfig``/``AgentConfig`` sección por sección con ``merged.get(...)``,
    así que una sección con typo (``schedulr:``) o un bloque entero en el nivel
    equivocado se ignoraban en silencio — el mismo fallo que la Fase 4 cerró
    para los niveles anidados.

    Para un agente, además, distingue el caso "bloque de nivel global": un
    ``scheduler:`` en ``agents/{id}.yaml`` no es un typo, es config del tier
    harness-global escrita donde ningún override es posible (ver "Tiers de
    recursos" en CLAUDE.md) — y el operador que la escribió cree que aplica.
    """
    from difflib import get_close_matches

    from core.domain.errors import ConfigError

    conocidas = set(modelo.model_fields)
    desconocidas = [k for k in raw if isinstance(k, str) and k not in conocidas]
    if not desconocidas:
        return

    globales = set(GlobalConfig.model_fields) - conocidas
    detalles = []
    for clave in sorted(desconocidas):
        if clave in globales:
            detalles.append(
                f"'{clave}' es config de nivel GLOBAL (harness): va en config/global.yaml, "
                f"acá no tiene efecto"
            )
            continue
        parecidas = get_close_matches(clave, conocidas, n=1, cutoff=0.6)
        sugerencia = f" ¿Quisiste decir '{parecidas[0]}'?" if parecidas else ""
        detalles.append(f"'{clave}' no existe{sugerencia}")
    raise ConfigError(f"{contexto}: {'; '.join(detalles)}.")


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
    Carga global.yaml (capa base, credenciales incluidas).
    Retorna (GlobalConfig, raw_dict) — el dict raw se usa para merge con agentes.
    """
    merged = _load_yaml_safe(config_dir / "global.yaml")

    _check_legacy_shape(merged)
    _check_top_level(merged, GlobalConfig, "config/global.yaml")
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
    # Solo los flags escalares: el bloque `channels` del global es dual en la
    # práctica (flags transversales + defaults de adapters que los agentes
    # heredan por el merge). Los sub-dicts son adapters y NO validan acá — el
    # espejo exacto de `_filter_channel_adapters`, que en el lado del agente
    # descarta los escalares. Sin este filtro pasaban dos cosas: el bloque
    # entero se ignoraba (thinking_indicator: true no hacía NADA, knob muerto
    # desde siempre) o, validado ingenuo, un `channels.telegram` global
    # legítimo abortaría el arranque.
    channels_global = ChannelsGlobalConfig(
        **{k: v for k, v in (merged.get("channels") or {}).items() if not isinstance(v, dict)}
    )
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
        channels=channels_global,
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
    (dict de adapters) comparten clave de YAML pero significan cosas distintas. El
    deep-merge propaga los flags globales al merged del agente; este filtro deja
    solo los valores que son dicts (los adapters como ``telegram`` o ``cli``) y
    descarta scalars.

    Sigue siendo necesario, y ahora es CRÍTICO: desde que ``AgentConfig`` valida
    cada bloque contra ``CHANNEL_SCHEMAS``, un flag global colado acá (p. ej.
    ``thinking_indicator``) ya no sería ruido inerte — abortaría el arranque como
    "canal desconocido". La colisión de nombre entre los dos bloques se salda en
    la Fase 7 del refactor de config; hasta entonces, este filtro la contiene.
    """
    return {k: v for k, v in raw.items() if isinstance(v, dict)}


# Único path donde el wiring LEE el bloque de broadcast. Cualquier otro lugar
# donde el operador lo escriba se descarta sin efecto.
_BROADCAST_PATH_VALIDO = "channels.telegram.broadcast"


def _avisar_broadcast_extraviado(agent_id: str, merged: dict) -> None:
    """Avisa si hay un bloque ``broadcast:`` en un nivel del YAML que nadie lee.

    ``_wire_broadcast_for_agent`` solo mira ``channels.telegram.broadcast``.
    Caso real: un `broadcast:` fuera de `channels.telegram` con la topología
    vieja (`port:` suelto). Ni el error de validación llegó a emitirse, porque
    el bloque nunca alcanzó el parser.

    Quedan dos ubicaciones equivocadas, y desde que ``AgentConfig`` valida los
    canales ya no se tratan igual:

    - **Raíz del agente**: ``assemble_agent_config`` solo copia ``channels``, así
      que el bloque se descarta sin que nada lo mire. Este warning es la ÚNICA
      señal — sigue siendo imprescindible.
    - **``channels.broadcast``**: ahora es un canal desconocido y la validación
      lo rechaza con su path. El warning corre antes y agrega lo que el error no
      sabe: cuál es el path válido.
    """
    extraviados = []
    if isinstance(merged.get("broadcast"), dict):
        extraviados.append("broadcast (raíz del agente)")
    canales = merged.get("channels")
    if isinstance(canales, dict) and isinstance(canales.get("broadcast"), dict):
        extraviados.append("channels.broadcast")

    if extraviados:
        logger.warning(
            "Agente '%s': bloque de broadcast en un nivel que NADIE lee (%s). El único "
            "path válido es '%s' — tal como está, el transporte no se levanta y el "
            "puerto queda cerrado.",
            agent_id,
            ", ".join(extraviados),
            _BROADCAST_PATH_VALIDO,
        )


def assemble_agent_config(merged: dict) -> AgentConfig:
    """Ensambla un ``AgentConfig`` desde un dict YA mergeado y resuelto.

    Asume que ``merged`` pasó por los merges de capas (``load_agent_config``) o por
    el builder efímero del flujo delegate (``AgentContainer.build_ephemeral_child``)
    y por la resolución de ``inherit``. Es el ÚNICO punto donde el mapeo
    dict → ``AgentConfig`` vive: lo comparten ambos callers.

    Lanza ``KeyError`` si falta un campo requerido (``id``/``name``/``description``)
    o ``ValueError`` si un sub-modelo es inválido. El caller decide la política:
    ``load_agent_config`` lo envuelve en ``ConfigError`` nombrando el fichero; el
    builder efímero propaga tal cual.
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
      global_raw → extra_base → agents/{id}.yaml

    ``extra_base`` son los defaults de rol (ej. sub-agentes, ver ``SUBAGENT_DEFAULTS``):
    pisan a ``global_raw`` pero el YAML explícito del agente sigue pisando por encima.

    Retorna ``None`` solo si el fichero NO EXISTE (caso legítimo: el caller
    pregunta por un agente que no está). Una config presente pero inválida
    levanta ``ConfigError`` y aborta el arranque: ver el comentario en el except.
    """
    agent_yaml = agents_dir / f"{agent_id}.yaml"

    if not agent_yaml.exists():
        logger.warning("Config del agente '%s' no encontrada: %s", agent_id, agent_yaml)
        return None

    agent_raw = _load_yaml_safe(agent_yaml)
    # El aviso ANTES del chequeo: si el top-level aborta por un `broadcast:`
    # suelto, el operador necesita leer también cuál es el path válido.
    _avisar_broadcast_extraviado(agent_id, agent_raw)
    # Sobre el fichero CRUDO del agente, antes del merge: tras mergear ya no se
    # puede distinguir lo que el agente declaró de lo que heredó del global.
    _check_top_level(agent_raw, AgentConfig, str(agent_yaml))

    if extra_base is not None:
        global_raw = _deep_merge(global_raw, extra_base)

    # Merge: global como base, agente como override
    merged = _deep_merge(global_raw, agent_raw)

    _check_legacy_shape(merged)

    try:
        return assemble_agent_config(merged)
    except (KeyError, ValueError) as exc:
        # ABORTA, no degrada. Antes esto era un WARNING y el agente simplemente
        # desaparecía del registry: el operador veía el daemon "sano", su bot sin
        # responder, y ninguna relación evidente entre las dos cosas. Un agente
        # que no existe es indistinguible de uno que nunca se configuró.
        from core.domain.errors import ConfigError

        raise ConfigError(
            f"Config inválida para el agente '{agent_id}' ({agent_yaml}): {exc}"
        ) from exc


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
        Delta crudo (SIN global_raw ni SUBAGENT_DEFAULTS) de un sub-agente. Lo usa
        el builder efímero (`build_ephemeral_child`) para resolver `inherit` contra
        el caller en tiempo de delegación — no contra `global_raw`.
        """
        return self._sub_agent_raw.get(agent_id)

    @staticmethod
    def _load_sub_agent_raw_delta(agent_id: str, sub_agents_dir: Path) -> dict:
        return _load_yaml_safe(sub_agents_dir / f"{agent_id}.yaml")

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
        tg_cfg = cfg.telegram
        if tg_cfg is not None and tg_cfg.token:
            telegram_tokens.setdefault(tg_cfg.token, []).append(agent_id)

        # Unicidad de broadcast.server.port dentro del mismo agente. Solo los
        # servers hacen bind(); un bloque con enabled=false no levanta transporte.
        broadcast_ports: dict[int, list[str]] = {}
        for channel_name, channel_cfg in cfg.channels.items():
            bc = getattr(channel_cfg, "broadcast", None)
            if bc is None or bc.enabled is False or bc.server is None:
                continue
            broadcast_ports.setdefault(bc.server.port, []).append(channel_name)

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
