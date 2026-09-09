"""Config → Settings VOs del kernel. El ÚNICO punto que conoce a los dos mundos.

Los use cases del kernel NO reciben ``AgentConfig``: declaran en un VO
(``inaki/kernel/domain/value_objects/agent_settings.py``) exactamente lo que
consumen, y acá, en el composition root, se mapea el schema user-facing a ese
vocabulario. Los renombres del mapeo son deliberados: ver el docstring del VO.
"""

from __future__ import annotations

from pathlib import Path

from inaki.channels.telegram.config import telegram_config
from inaki.config import AgentConfig
from inaki.config.home import get_inaki_home
from inaki.kernel.domain.value_objects.agent_settings import OneShotSettings, RunAgentSettings
from inaki.memory.wiring import build_memory_settings


def build_run_agent_settings(
    cfg: AgentConfig, *, user_timezone: str | None = None
) -> RunAgentSettings:
    """``user_timezone`` viene del bloque GLOBAL ``user`` (no existe en ``AgentConfig``):
    el ensamblador lo pasa; los tests que construyen el VO sin él caen a la TZ local."""
    tg_cfg = telegram_config(cfg)
    timestamp_channels = (
        frozenset({"telegram"}) if tg_cfg is not None and tg_cfg.add_llm_timestamp else frozenset()
    )
    return RunAgentSettings(
        agent_id=cfg.id,
        name=cfg.name,
        description=cfg.description,
        system_prompt=cfg.system_prompt,
        workspace_root=str(Path(cfg.workspace.path).expanduser().resolve()),
        users_dir=str(get_inaki_home() / "users"),
        include_base_dir=str(get_inaki_home()),
        merge_chats=cfg.chat_history.merge_chats,
        min_words_threshold=cfg.semantic_routing.min_words_threshold,
        skills_min_skills=cfg.skills.semantic_routing_min_skills,
        skills_top_k=cfg.skills.semantic_routing_top_k,
        skills_min_score=cfg.skills.semantic_routing_min_score,
        skills_sticky_ttl=cfg.skills.sticky_ttl,
        tools_min_tools=cfg.tools.semantic_routing_min_tools,
        tools_top_k=cfg.tools.semantic_routing_top_k,
        tools_min_score=cfg.tools.semantic_routing_min_score,
        tools_sticky_ttl=cfg.tools.sticky_ttl,
        tools_pinned=frozenset(cfg.tools.pinned),
        tool_call_max_iterations=cfg.tools.tool_call_max_iterations,
        circuit_breaker_threshold=cfg.tools.circuit_breaker_threshold,
        request_delay_seconds=cfg.llm.request_delay_seconds,
        timestamp_channels=timestamp_channels,
        persist_tool_calls=cfg.chat_history.persist_tool_calls,
        persist_tool_result_max_chars=cfg.chat_history.persist_tool_result_max_chars,
        memory=build_memory_settings(cfg.memories),
        user_timezone=user_timezone,
    )


def build_one_shot_settings(cfg: AgentConfig) -> OneShotSettings:
    return OneShotSettings(
        agent_id=cfg.id,
        system_prompt=cfg.system_prompt,
        circuit_breaker_threshold=cfg.tools.circuit_breaker_threshold,
        request_delay_seconds=cfg.llm.request_delay_seconds,
    )
