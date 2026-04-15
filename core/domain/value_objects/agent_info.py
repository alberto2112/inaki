"""DTO de solo lectura con info pública del agente."""

from __future__ import annotations

from typing import NamedTuple


class AgentInfoDTO(NamedTuple):
    """DTO de solo lectura con info pública del agente."""

    id: str
    name: str
    description: str
