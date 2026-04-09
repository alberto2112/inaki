from __future__ import annotations

from typing import Protocol


class ILLMDispatcher(Protocol):
    async def dispatch(
        self,
        agent_id: str,
        prompt: str | None = None,
        tools_override: list[dict] | None = None,
    ) -> str: ...
