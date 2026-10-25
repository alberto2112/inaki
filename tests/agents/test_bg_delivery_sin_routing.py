"""Regresión bg-1001: la entrega de un resultado enorme no puede pasar por el embedder.

Incidente: una delegación terminó bien y su resultado (60.388 chars) se persistió, pero
entregarlo corría un turno completo del padre que embebía el ``[bg-N]`` ENTERO contra
``text-embedding-3-small`` (máx. 8192 tokens) → HTTP 400 → los 3 reintentos del dispatch
fallaban igual → ``delivery_failed``. Fix: la entrega no rutea (``skip_routing``).

Cableado real de punta a punta: cola → ``LLMDispatcherAdapter`` → ``RunAgentUseCase``;
solo el embedder (con el límite de OpenAI), el LLM y los repos están simulados.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from inaki.agents.delegation.background_queue import BackgroundDelegationQueueAdapter
from inaki.agents.dispatcher import LLMDispatcherAdapter
from inaki.kernel.domain.agent_settings import RunAgentSettings
from inaki.kernel.domain.llm_response import LLMResponse
from inaki.kernel.run_agent import RunAgentUseCase
from inaki.shared.errors import EmbeddingError

_LIMITE_CHARS = 30_000  # holgadamente bajo los 60.388 del incidente


def _embedder_con_limite() -> AsyncMock:
    async def embed_query(texto: str) -> list[float]:
        if len(texto) > _LIMITE_CHARS:
            raise EmbeddingError("OpenAI embeddings HTTP error: Client error '400 Bad Request'")
        return [0.1] * 384

    embedder = AsyncMock()
    embedder.embed_query = AsyncMock(side_effect=embed_query)
    return embedder


async def test_resultado_enorme_se_entrega_sin_embeber(
    mock_llm, mock_memory, mock_skills, mock_history, mock_tools
):
    mock_skills.list_all.return_value = []
    mock_tools.get_schemas.return_value = [
        {"type": "function", "function": {"name": f"t{i}", "description": "d"}} for i in range(3)
    ]
    mock_llm.complete.return_value = LLMResponse.of_text("Listo, te resumo el resultado.")
    embedder = _embedder_con_limite()
    run_agent = RunAgentUseCase(
        llm=mock_llm,
        memory=mock_memory,
        embedder=embedder,
        skills=mock_skills,
        history=mock_history,
        tools=mock_tools,
        settings=RunAgentSettings(agent_id="inaki", tools_min_tools=1, skills_min_skills=0),
    )
    agente = MagicMock()
    agente.run_agent = run_agent
    dispatcher = LLMDispatcherAdapter({"inaki": agente})

    resultado_enorme = '```json\n{"status": "success", "summary": "ok", "details": "%s"}\n```' % (
        "x" * 60_000
    )
    one_shot = MagicMock()
    one_shot.system_prompt = "Sos el hijo."
    one_shot.execute = AsyncMock(return_value=resultado_enorme)
    adapter = BackgroundDelegationQueueAdapter(
        dispatcher=dispatcher,
        one_shot_resolver=lambda caller, target: one_shot,
        max_concurrent=1,
    )
    await adapter.start()
    enqueued = await adapter.enqueue(
        caller_agent_id="inaki",
        target_agent_id="r",
        prompt="investigá",
        system_prompt=None,
        channel="telegram",
        chat_id="42",
        max_iterations=5,
        timeout_seconds=30,
    )
    # Espera a que la task llegue a un estado final (entregada y purgada, o fallida).
    for _ in range(500):
        task = adapter._tasks.get(enqueued.task_id)
        if task is None or task.status in ("delivered", "delivery_failed"):
            break
        await asyncio.sleep(0.01)
    await adapter.stop()

    embedder.embed_query.assert_not_called()
    # El padre SÍ vio el resultado entero (la entrega no lo recorta).
    mensajes = mock_llm.complete.call_args.args[0]
    assert any(len(m.content) > 60_000 for m in mensajes if m.content)
    # Y la task terminó entregada, no atascada en delivery_failed.
    assert enqueued.task_id not in adapter._tasks or (
        adapter._tasks[enqueued.task_id].status == "delivered"
    )
