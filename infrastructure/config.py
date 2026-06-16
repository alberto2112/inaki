"""Fachada de configuración de Inaki — punto de import único.

El schema (modelos Pydantic) vive en ``config_schema`` y la carga/merge en
``config_loader``. Este módulo los reexporta para preservar el contrato
histórico ``from infrastructure.config import X`` sin que el resto del código
tenga que conocer el split. NO agregar lógica acá: schema → config_schema,
carga → config_loader.
"""

from __future__ import annotations

from infrastructure.config_schema import *  # noqa: F401,F403
from infrastructure.config_loader import *  # noqa: F401,F403
from infrastructure.config_schema import (  # noqa: F401
    _LLM_TIMEOUT_FALLBACK,
    _SQLITE_SPECIAL,
    _expand_user_list,
    _expand_user_str,
    _resolve_runtime_path,
)
from infrastructure.config_loader import (  # noqa: F401
    _DELEGATION_SECTION_COMMENT,
    _GLOBAL_YAML_HEADER,
    _LEGACY_ERROR_TEMPLATE,
    _LEGACY_FIELDS,
    _SECRETS_YAML_HEADER,
    _HasChannels,
    _check_legacy_shape,
    _deep_merge,
    _filter_channel_adapters,
    _load_yaml_safe,
    _parse_providers,
    _render_default_global_yaml,
    _validate_channel_uniqueness,
)
