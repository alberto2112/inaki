"""Schema de configuración de Inaki — modelos Pydantic, una sección por área.

Punto de import único del schema: ``from inaki.config.schema import GlobalConfig``.
Cada sección vive en su fichero para que, cuando un módulo se extraiga (canales,
memoria, scheduler...), su bloque de config se mude con él.
"""

from __future__ import annotations

from inaki.config.schema._base import ExpandedPath, ExpandedPathList, RuntimePath, _ConfigBaseModel
from inaki.config.schema.admin import AdminConfig
from inaki.config.schema.app import AppConfig
from inaki.config.schema.channels import (
    ChannelFallbackConfig,
    ChannelsGlobalConfig,
    CliChannelConfig,
)
from inaki.config.schema.chat_history import ChatHistoryConfig
from inaki.config.schema.delegation import AgentDelegationConfig, DelegationConfig
from inaki.config.schema.embedding import EmbeddingConfig
from inaki.config.schema.knowledge import KnowledgeConfig, KnowledgeSourceConfig
from inaki.config.schema.llm import LLMConfig
from inaki.config.schema.memories import (
    ConsolidationConfig,
    MemoriesConfig,
    MemoryLLMConfig,
    ReconciliationConfig,
)
from inaki.config.schema.photos import DedupConfig, FacesConfig, PhotosConfig, SceneConfig
from inaki.config.schema.providers import ProviderConfig
from inaki.config.schema.root import AgentConfig, CHANNEL_SCHEMAS, GlobalConfig
from inaki.config.schema.scheduler import SchedulerConfig
from inaki.config.schema.skills import SkillsConfig
from inaki.config.schema.telegram import (
    BroadcastClientConfig,
    BroadcastConfig,
    BroadcastEmitConfig,
    BroadcastServerConfig,
    TelegramChannelConfig,
    TelegramGroupsConfig,
)
from inaki.config.schema.tools import SemanticRoutingConfig, ToolsConfig
from inaki.config.schema.transcription import TranscriptionConfig
from inaki.config.schema.user import UserConfig
from inaki.config.schema.workspace import ContainmentMode, WorkspaceConfig

__all__ = [
    "AdminConfig",
    "AgentConfig",
    "AgentDelegationConfig",
    "AppConfig",
    "BroadcastClientConfig",
    "BroadcastConfig",
    "BroadcastEmitConfig",
    "BroadcastServerConfig",
    "CHANNEL_SCHEMAS",
    "ChannelFallbackConfig",
    "ChannelsGlobalConfig",
    "ChatHistoryConfig",
    "CliChannelConfig",
    "ConsolidationConfig",
    "ContainmentMode",
    "DedupConfig",
    "DelegationConfig",
    "EmbeddingConfig",
    "ExpandedPath",
    "ExpandedPathList",
    "FacesConfig",
    "GlobalConfig",
    "KnowledgeConfig",
    "KnowledgeSourceConfig",
    "LLMConfig",
    "MemoriesConfig",
    "MemoryLLMConfig",
    "PhotosConfig",
    "ProviderConfig",
    "ReconciliationConfig",
    "RuntimePath",
    "SceneConfig",
    "SchedulerConfig",
    "SemanticRoutingConfig",
    "SkillsConfig",
    "TelegramChannelConfig",
    "TelegramGroupsConfig",
    "ToolsConfig",
    "TranscriptionConfig",
    "UserConfig",
    "WorkspaceConfig",
    "_ConfigBaseModel",
]
