"""Wiring del módulo tools: el store del Tool Config Protocol, el workspace y los builtins.

Único fichero del módulo con permiso para importar ``inaki.config``. Las tools
de un módulo de feature las arma el wiring de ESE módulo (memoria, knowledge,
perception, canales); acá solo las que no pertenecen a ninguno.
"""

from __future__ import annotations

import logging
from pathlib import Path

from inaki.config import AgentConfig
from inaki.kernel.ports.tool_config_port import IToolConfigStore
from inaki.kernel.ports.tool_port import ITool
from inaki.tools.builtin.edit_file import EditFileTool
from inaki.tools.builtin.patch_file import PatchFileTool
from inaki.tools.builtin.read_file import ReadFileTool
from inaki.tools.builtin.web_search import WebSearchTool
from inaki.tools.builtin.write_file import WriteFileTool
from inaki.tools.config_store import YamlToolConfigStore

logger = logging.getLogger(__name__)


def build_tool_config_store(config_dir: Path) -> YamlToolConfigStore:
    """El store compartido por namespace (``config/tool_config.yaml``) y su clave Fernet."""
    return YamlToolConfigStore(
        store_path=config_dir / "tool_config.yaml",
        key_path=config_dir.parent / "secret.key",
    )


def resolver_workspace(cfg: AgentConfig) -> Path:
    """Resuelve y crea el workspace del agente. Un workspace que no se puede crear
    aborta la construcción del agente: sin él no hay file tools que valgan."""
    workspace = Path(cfg.workspace.path).expanduser().resolve()
    try:
        workspace.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.error(
            "No se pudo crear el workspace '%s' para el agente '%s': %s", workspace, cfg.id, exc
        )
        raise
    logger.info(
        "Agente '%s': workspace='%s' containment='%s'", cfg.id, workspace, cfg.workspace.containment
    )
    return workspace


def build_builtin_tools(
    cfg: AgentConfig, *, workspace: Path, config_store: IToolConfigStore
) -> list[ITool]:
    """Búsqueda web (Tool Config Protocol) y las cuatro file tools sobre el workspace."""
    containment = cfg.workspace.containment
    return [
        WebSearchTool(config_store=config_store),
        ReadFileTool(workspace=workspace, containment=containment),
        WriteFileTool(workspace=workspace, containment=containment),
        PatchFileTool(workspace=workspace, containment=containment),
        EditFileTool(workspace=workspace, containment=containment),
    ]
