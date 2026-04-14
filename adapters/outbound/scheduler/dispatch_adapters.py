from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

from core.ports.outbound.llm_dispatcher_port import ILLMDispatcher
from core.use_cases.consolidate_all_agents import ConsolidateAllAgentsUseCase

if TYPE_CHECKING:
    from core.domain.entities.task import WebhookPayload

# Canales que son inbound (CLI, REST, daemon) pero no tienen gateway de salida.
_CANALES_INBOUND = {"cli", "rest", "daemon"}


class ChannelSenderAdapter:
    """Enruta el prefijo de canal al gateway correspondiente.

    Args:
        get_telegram_bot: Callable que devuelve el bot de Telegram en el momento
            de uso (lazy). Desacopla el adaptador del contenedor y facilita el testing.
    """

    def __init__(self, get_telegram_bot: Callable) -> None:
        self._get_telegram_bot = get_telegram_bot

    async def send_message(self, target: str, text: str) -> None:
        """Envía ``text`` al destino indicado por ``target``.

        El formato de ``target`` es ``"<prefijo>:<destino>"``, por ejemplo
        ``"telegram:12345"`` o ``"cli:stdout"``.

        Raises:
            ValueError: Si el prefijo no es ``"telegram"`` o es desconocido.
        """
        prefix, _, destination = target.partition(":")
        if prefix == "telegram":
            bot = self._get_telegram_bot()
            if bot is None:
                raise ValueError(
                    "Telegram no está configurado. "
                    "El bot no fue registrado en el sistema."
                )
            await bot.send_message(int(destination), text)
        elif prefix in _CANALES_INBOUND:
            raise ValueError(
                f"channel_send no soportado para canal '{prefix}'. "
                "Solo 'telegram' está implementado."
            )
        else:
            raise ValueError(f"Prefijo de canal desconocido: '{prefix}'")


class LLMDispatcherAdapter:
    def __init__(self, agents: dict) -> None:
        self._agents = agents

    async def dispatch(
        self,
        agent_id: str,
        prompt: str | None = None,
        tools_override: list[dict] | None = None,
    ) -> str:
        agent = self._agents.get(agent_id)
        if agent is None:
            raise ValueError(f"Agent '{agent_id}' not found")
        return await agent.run_agent.run(prompt or "", tools_override=tools_override)


class ConsolidationDispatchAdapter:
    """Thin wrapper so the scheduler service doesn't import the use case directly."""

    def __init__(self, use_case: ConsolidateAllAgentsUseCase) -> None:
        self._uc = use_case

    async def consolidate_all(self) -> str:
        return await self._uc.execute()


class HttpCallerAdapter:
    """Performs HTTP calls for webhook triggers."""

    async def call(self, payload: WebhookPayload) -> str:
        async with httpx.AsyncClient() as client:
            try:
                response = await client.request(
                    method=payload.method,
                    url=payload.url,
                    headers=payload.headers,
                    content=payload.body,
                    timeout=payload.timeout,
                )
            except httpx.TimeoutException as exc:
                raise RuntimeError(f"Webhook timed out: {exc}") from exc
            except httpx.ConnectError as exc:
                raise RuntimeError(f"Webhook connection failed: {exc}") from exc
            if response.status_code not in payload.success_codes:
                raise RuntimeError(
                    f"Webhook returned non-success status {response.status_code}"
                )
            return response.text


@dataclass
class SchedulerDispatchPorts:
    channel_sender: ChannelSenderAdapter
    llm_dispatcher: ILLMDispatcher
    consolidator: ConsolidationDispatchAdapter
    http_caller: HttpCallerAdapter
