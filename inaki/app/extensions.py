"""Registro de extensiones: lo que declara cada ``manifest.py`` va a tres registros.

Es del composition root porque una extensión aporta a TRES módulos a la vez
(tools, skills, knowledge) y ninguno de los tres debe conocer a los otros. El
descubrimiento (recorrer ``app.ext_dirs``, importar manifests) es de
``inaki.extensions``; acá solo se instancia y se registra.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from inaki.config import AgentConfig, GlobalConfig
from inaki.extensions import descubrir_extensiones
from inaki.kernel.ports.embedding_port import IEmbeddingProvider
from inaki.kernel.ports.knowledge_port import IKnowledgeSource
from inaki.kernel.ports.tool_config_port import IToolConfigStore
from inaki.skills.yaml_skill_repo import YamlSkillRepository
from inaki.tools.registry import ToolRegistry, instanciar_tool

logger = logging.getLogger(__name__)


def registrar_extensiones(
    ext_dirs: Sequence[str],
    *,
    tools: ToolRegistry,
    skills: YamlSkillRepository,
    knowledge_sources: list[IKnowledgeSource],
    config_store: IToolConfigStore,
    agent_cfg: AgentConfig,
    global_cfg: GlobalConfig,
    embedder: IEmbeddingProvider,
) -> None:
    """Tools (con el Tool Config Protocol), skills y fuentes de knowledge de nivel (3).

    ``knowledge_sources`` es la lista VIVA que ya tiene el orquestador: las
    fuentes de extensiones entran detrás de (1) memoria y (2) config sin
    reconstruirlo. Una tool que colisiona con una ya registrada se saltea con
    ``WARNING``: los builtins ganan.
    """
    for ext in descubrir_extensiones(ext_dirs):
        for tool_cls in ext.tools:
            try:
                tool = instanciar_tool(tool_cls, config_store=config_store)
            except Exception as exc:
                logger.warning(
                    "Extensión '%s': falló al instanciar %r (%s) — skipping tool",
                    ext.nombre,
                    tool_cls,
                    exc,
                )
                continue
            if tool.name in tools:
                logger.warning(
                    "Extensión '%s': tool '%s' ya registrada — skipping (colisión)",
                    ext.nombre,
                    tool.name,
                )
                continue
            tools.register(tool)
            logger.info("Extensión '%s': tool '%s' registrada", ext.nombre, tool.name)
        for skill_path in ext.skills:
            skills.add_file(skill_path)
            logger.info("Extensión '%s': skill '%s' añadida", ext.nombre, skill_path.name)
        for factory in ext.knowledge_sources:
            try:
                fuente = factory(agent_cfg, global_cfg, embedder)
            except Exception as exc:
                logger.warning(
                    "Extensión '%s': factory de knowledge source falló (%s) — skipping",
                    ext.nombre,
                    exc,
                )
                continue
            knowledge_sources.append(fuente)
            logger.info(
                "Extensión '%s': knowledge source '%s' registrada", ext.nombre, fuente.source_id
            )
