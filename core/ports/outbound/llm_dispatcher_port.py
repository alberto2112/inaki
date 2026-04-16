from __future__ import annotations

from typing import Protocol

from core.ports.outbound.intermediate_sink_port import IIntermediateSink


class ILLMDispatcher(Protocol):
    async def dispatch(
        self,
        agent_id: str,
        prompt: str | None = None,
        tools_override: list[dict] | None = None,
        intermediate_sink: IIntermediateSink | None = None,
    ) -> str: ...
