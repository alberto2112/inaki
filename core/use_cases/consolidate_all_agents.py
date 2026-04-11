"""
ConsolidateAllAgentsUseCase — consolida la memoria de todos los agentes habilitados.

Iterador global que recorre el mapa de agentes habilitados (aquellos cuyo
`memory.enabled` es true en su config resuelta), invoca el use case de
consolidación per-agente secuencialmente y duerme `delay_seconds` entre
agente y agente para respetar rate limits del proveedor LLM.

Disparado por:
  - el scheduler nocturno (payload ConsolidateMemoryPayload)
  - el flag CLI `inaki --consolidate` sin --agent

Nunca lanza excepciones al caller — recolecta los fallos por agente y los
devuelve en el mensaje final.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from core.domain.errors import ConsolidationError
from core.use_cases.consolidate_memory import ConsolidateMemoryUseCase

logger = logging.getLogger(__name__)


@dataclass
class ConsolidateAllResult:
    succeeded: dict[str, str] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.succeeded) + len(self.failed)

    def format(self) -> str:
        if self.total == 0:
            return "No hay agentes con memoria habilitada."
        lines = [f"Consolidación global: {self.total} agente(s) procesado(s)"]
        for agent_id, msg in self.succeeded.items():
            lines.append(f"  ✓ {agent_id}: {msg}")
        for agent_id, err in self.failed.items():
            lines.append(f"  ✗ {agent_id}: {err}")
        return "\n".join(lines)


class ConsolidateAllAgentsUseCase:

    def __init__(
        self,
        enabled_agents: dict[str, ConsolidateMemoryUseCase],
        delay_seconds: int,
    ) -> None:
        self._agents = enabled_agents
        self._delay = max(0, int(delay_seconds))

    async def execute(self) -> str:
        result = ConsolidateAllResult()

        if not self._agents:
            logger.info("Consolidación global: ningún agente con memoria habilitada")
            return result.format()

        agent_ids = list(self._agents.keys())
        logger.info(
            "Consolidación global arrancando: %d agente(s) habilitado(s): %s",
            len(agent_ids),
            agent_ids,
        )

        for idx, agent_id in enumerate(agent_ids):
            uc = self._agents[agent_id]
            logger.info("Consolidando memoria de '%s'...", agent_id)
            try:
                msg = await uc.execute()
                result.succeeded[agent_id] = msg
                logger.info("Agente '%s' consolidado: %s", agent_id, msg)
            except ConsolidationError as exc:
                result.failed[agent_id] = str(exc)
                logger.error("Agente '%s' falló en consolidación: %s", agent_id, exc)
            except Exception as exc:  # noqa: BLE001 — no reventar al scheduler por un agente
                result.failed[agent_id] = f"error inesperado: {exc}"
                logger.exception("Agente '%s' falló con excepción no manejada", agent_id)

            is_last = idx == len(agent_ids) - 1
            if not is_last and self._delay > 0:
                await asyncio.sleep(self._delay)

        return result.format()
