"""
Implementación de ``IConfigRepository`` usando ``ruamel.yaml`` en modo round-trip.

El adapter preserva comentarios, orden de claves y anchors al escribir.
El flujo de escritura es atómico: escribe a un archivo temporal y luego
hace ``os.replace()`` para que la sustitución sea atómica a nivel del SO.

Los archivos ``*.secrets.yaml`` se crean con permisos ``600`` (solo el
propietario puede leer/escribir).
"""

from __future__ import annotations

import io
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from core.ports.config_repository import LayerName


# ---------------------------------------------------------------------------
# Headers de creación para archivos nuevos
# ---------------------------------------------------------------------------

_HEADER_GLOBAL = """\
# Generado por inaki setup
# Config global — ~/.inaki/config/global.yaml
# Editá este archivo a mano o usá `inaki setup` para modificarlo con la TUI.
"""

_HEADER_GLOBAL_SECRETS = """\
# Generado por inaki setup — SECRETO
# Secrets globales — ~/.inaki/config/global.secrets.yaml
# NUNCA commitees este archivo. Contiene API keys y credenciales compartidas.
"""

_HEADER_AGENT = """\
# Generado por inaki setup
# Config de agente — este archivo fue creado con `inaki setup`.
"""

_HEADER_AGENT_SECRETS = """\
# Generado por inaki setup — SECRETO
# Secrets del agente — contiene tokens y claves específicas de este agente.
# NUNCA commitees este archivo.
"""

_HEADER_SUB_AGENT = """\
# Generado por inaki setup
# Config de sub-agente — solo para delegación, sin canales.
# Este archivo fue creado con `inaki setup`.
"""

_HEADER_SUB_AGENT_SECRETS = """\
# Generado por inaki setup — SECRETO
# Secrets del sub-agente — contiene tokens y claves específicas de este sub-agente.
# NUNCA commitees este archivo.
"""

_HEADERS: dict[LayerName, str] = {
    LayerName.GLOBAL: _HEADER_GLOBAL,
    LayerName.GLOBAL_SECRETS: _HEADER_GLOBAL_SECRETS,
    LayerName.AGENT: _HEADER_AGENT,
    LayerName.AGENT_SECRETS: _HEADER_AGENT_SECRETS,
    LayerName.SUB_AGENT: _HEADER_SUB_AGENT,
    LayerName.SUB_AGENT_SECRETS: _HEADER_SUB_AGENT_SECRETS,
}

_SECRETS_LAYERS: frozenset[LayerName] = frozenset(
    {LayerName.GLOBAL_SECRETS, LayerName.AGENT_SECRETS, LayerName.SUB_AGENT_SECRETS}
)


class YamlRepository:
    """
    Repositorio YAML con preservación de comentarios usando ruamel.yaml round-trip.

    Implementa ``IConfigRepository`` a partir de los 4 archivos de configuración
    en ``~/.inaki/config/``. Cada llamada a ``write_layer`` hace una escritura
    atómica con ``os.replace()`` y preserva comentarios, orden de claves y
    anchors YAML.

    Args:
        config_dir: Directorio raíz de configuración. Si es ``None``, se
            resuelve automáticamente via ``paths.get_config_dir()``.
    """

    def __init__(self, config_dir: Path | None = None) -> None:
        if config_dir is None:
            # Layout default del runtime: ~/.inaki/config/global*.yaml +
            # ~/.inaki/agents/{id}.yaml (sibling, NO subcarpeta de config/).
            from .paths import get_agents_dir, get_config_dir

            self._config_dir = get_config_dir()
            self._agents_dir = get_agents_dir()
        else:
            # Override explícito (tests con tmp_path o legacy --config DIR):
            # agentes bajo config_dir/agents/, layout unificado.
            self._config_dir = config_dir
            self._agents_dir = config_dir / "agents"
        # Sub-agentes viven en un subdirectorio reservado por el runtime
        # (mismo layout que el config_loader: ``agents/sub-agents/``).
        self._sub_agents_dir = self._agents_dir / "sub-agents"
        self._yaml = YAML(typ="rt")
        self._yaml.preserve_quotes = True
        self._yaml.width = 4096  # Evita el line-wrapping inesperado

    # ------------------------------------------------------------------
    # Resolución de rutas internas (relativas a config_dir inyectado)
    # ------------------------------------------------------------------

    def _layer_path(self, layer: LayerName, agent_id: str | None) -> Path:
        """Devuelve el Path del archivo para la capa indicada."""
        match layer:
            case LayerName.GLOBAL:
                return self._config_dir / "global.yaml"
            case LayerName.GLOBAL_SECRETS:
                return self._config_dir / "global.secrets.yaml"
            case LayerName.AGENT:
                self._require_agent_id(agent_id)
                return self._agents_dir / f"{agent_id}.yaml"
            case LayerName.AGENT_SECRETS:
                self._require_agent_id(agent_id)
                return self._agents_dir / f"{agent_id}.secrets.yaml"
            case LayerName.SUB_AGENT:
                self._require_agent_id(agent_id)
                return self._sub_agents_dir / f"{agent_id}.yaml"
            case LayerName.SUB_AGENT_SECRETS:
                self._require_agent_id(agent_id)
                return self._sub_agents_dir / f"{agent_id}.secrets.yaml"

    @staticmethod
    def _require_agent_id(agent_id: str | None) -> None:
        if not agent_id:
            raise ValueError(
                "agent_id es requerido para capas LayerName.AGENT y LayerName.AGENT_SECRETS"
            )

    # ------------------------------------------------------------------
    # IConfigRepository — implementación
    # ------------------------------------------------------------------

    def read_layer(self, layer: LayerName, agent_id: str | None = None) -> dict:
        """
        Lee la capa indicada y la devuelve como dict (puede ser CommentedMap).

        Si el archivo no existe, devuelve ``{}`` sin error.
        """
        path = self._layer_path(layer, agent_id)
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as f:
            data = self._yaml.load(f)
        if data is None:
            return {}
        return data  # type: ignore[return-value]

    def write_layer(self, layer: LayerName, data: dict, agent_id: str | None = None) -> None:
        """
        Escribe ``data`` en la capa indicada preservando comentarios y orden.

        Si el archivo no existe lo crea con un header comment apropiado.
        La escritura es atómica: tmp file → ``os.replace()``.
        Los archivos de secrets se crean/protegen con permisos ``600``.

        Args:
            layer: Capa de destino.
            data: Contenido a escribir. Puede ser un ``CommentedMap`` (para
                preservar comentarios) o un dict plano.
            agent_id: Requerido para capas de agente.
        """
        path = self._layer_path(layer, agent_id)
        es_archivo_nuevo = not path.exists()
        es_secrets = layer in _SECRETS_LAYERS

        # Asegurá que el directorio padre exista
        path.parent.mkdir(parents=True, exist_ok=True)

        # Prepará el CommentedMap o dict final a escribir
        if es_archivo_nuevo:
            header = _HEADERS[layer]
            cmap = _ensure_commented_map(data)
            # Adjuntá el header como comentario al inicio del documento
            cmap.yaml_set_start_comment(header.strip())
        else:
            cmap = _ensure_commented_map(data)

        # Serializá a string en memoria
        buf = io.StringIO()
        self._yaml.dump(cmap, buf)
        contenido = buf.getvalue()

        # Escritura atómica en el mismo directorio (mismo filesystem → rename es atómico)
        fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp", prefix=".inaki-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(contenido)

            if es_secrets:
                os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)  # 600

            os.replace(tmp_path, path)

            # Si el archivo ya existía pero era de secrets, asegurar permisos
            if es_secrets and not es_archivo_nuevo:
                os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)

        except Exception:
            # Limpieza si algo falla antes del rename
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def list_agents(self) -> list[str]:
        """
        Enumera los ids de agentes regulares en el directorio de agentes.

        Retorna una lista ordenada de ids (stems de ``{id}.yaml``),
        excluyendo ``*.secrets.yaml`` y ``*.example.yaml``.
        Lista vacía si el directorio no existe o no tiene agentes.

        NO incluye los sub-agentes (``agents/sub-agents/``) — el glob es plano,
        no recursivo, y ``sub-agents`` es un directorio (no matchea ``*.yaml``).
        """
        return self._list_agent_ids(self._agents_dir)

    def list_sub_agents(self) -> list[str]:
        """
        Enumera los ids de sub-agentes en ``agents/sub-agents/``.

        Mismo contrato que ``list_agents`` pero sobre el subdirectorio de
        sub-agentes. Lista vacía si el subdirectorio no existe.
        """
        return self._list_agent_ids(self._sub_agents_dir)

    @staticmethod
    def _list_agent_ids(directory: Path) -> list[str]:
        """Glob plano de ``{id}.yaml`` en ``directory``, excluyendo secrets/example."""
        if not directory.exists():
            return []
        return sorted(
            p.stem
            for p in directory.glob("*.yaml")
            if ".secrets" not in p.name and ".example" not in p.name
        )

    def layer_exists(self, layer: LayerName, agent_id: str | None = None) -> bool:
        """
        Retorna ``True`` si el archivo de la capa existe en disco.
        """
        return self._layer_path(layer, agent_id).exists()

    def delete_layer(self, layer: LayerName, agent_id: str | None = None) -> None:
        """
        Elimina el archivo de la capa indicada si existe.

        Idempotente — no lanza error si el archivo no existe.
        """
        path = self._layer_path(layer, agent_id)
        try:
            path.unlink()
        except FileNotFoundError:
            pass  # no-op idempotente

    def render_yaml(self, data: dict) -> str:
        """
        Serializa ``data`` a string YAML sin escribir a disco.

        Útil para generar el diff preview antes de confirmar un guardado.
        """
        buf = io.StringIO()
        self._yaml.dump(_ensure_commented_map(data), buf)
        return buf.getvalue()


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------


def _ensure_commented_map(data: Any) -> CommentedMap:
    """
    Convierte ``data`` a ``CommentedMap`` si no lo es ya.

    Si ya es un ``CommentedMap`` (p. ej. leído via ruamel.yaml round-trip),
    lo devuelve sin tocar para preservar comentarios. Los dicts planos se
    convierten superficialmente (sin recursión profunda, para no perder la
    estructura que ya tiene ruamel en submapas).
    """
    if isinstance(data, CommentedMap):
        return data
    cm = CommentedMap()
    cm.update(data)
    return cm
