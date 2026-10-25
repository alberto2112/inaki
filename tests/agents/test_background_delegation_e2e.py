"""Integration end-to-end del feature ``background-delegation``.

Cubre el flujo completo con adapters REALES (no mockeados):
1. ``BackgroundDelegationQueueAdapter.enqueue(...)`` registra la task y devuelve ``bg-N``
2. El consumer real toma la task, la ejecuta vía ``RunAgentOneShotUseCase.execute``
   (mockeado para devolver texto controlado)
3. El consumer invoca ``LLMDispatcherAdapter.dispatch(...)`` con marker ``[bg-N] ...``
4. El dispatcher serializa por scope (lock-per-scope) y reentra en
   ``run_agent.execute`` que persiste el mensaje en historial

Mocks solo en el LLM/provider boundary y en el ``run_agent_one_shot`` del agente
hijo. La cola, el dispatcher y el run_agent del padre son reales.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from inaki.agents.delegation.background_queue import (
    BackgroundDelegationQueueAdapter,
)
from inaki.agents.dispatcher import LLMDispatcherAdapter
from inaki.shared.message import Role


async def test_end_to_end_async_delegation() -> None:
    """E2E: enqueue → consumer → dispatch → run_agent (real) persiste [bg-N] ...

    Verifica REQ-BGD-1, REQ-BGD-2, REQ-BGD-4, REQ-BGD-5, REQ-BGD-6 (lock vía
    dispatcher), REQ-DG-11 (marker).
    """
    # ----- Setup: agente caller real + agente target (one_shot mockeado) -----
    historial_persistido: list[tuple[Role, str]] = []

    async def caller_execute(prompt, **_kw) -> str:
        """Stub del ``run_agent.execute`` del padre: registra el prompt como USER
        en el historial y devuelve un acuse. Simula el efecto de inyectar el
        resultado del bg-task de vuelta a la conversación.
        """
        historial_persistido.append((Role.USER, prompt))
        return "acuse"

    caller = MagicMock()
    caller.run_agent.execute = AsyncMock(side_effect=caller_execute)

    target_one_shot = MagicMock()
    target_one_shot.system_prompt = "Sos el investigador."
    # El hijo cierra con el bloque JSON que exige el footer (REQ-BGD-5): el padre recibe el
    # resultado estructurado, no el texto crudo.
    target_one_shot.execute = AsyncMock(
        return_value='```json\n{"status": "success", "summary": "Saldo $5.420", '
        '"details": null, "reason": null}\n```'
    )

    def _resolve_one_shot(caller_id: str, target_id: str):
        return target_one_shot if target_id == "researcher" else None

    # Dispatcher real (lock-per-scope incluido)
    agents = {"inaki": caller}
    dispatcher = LLMDispatcherAdapter(agents)

    # Cola real con consumer
    queue = BackgroundDelegationQueueAdapter(
        dispatcher=dispatcher,
        one_shot_resolver=_resolve_one_shot,
        max_concurrent=3,
    )

    await queue.start()
    try:
        # ----- Acto: enqueue como lo haría DelegateTool en wait=False -----
        task_id = (
            await queue.enqueue(
                caller_agent_id="inaki",
                target_agent_id="researcher",
                prompt="averiguá el saldo de Galicia",
                system_prompt=None,
                channel="telegram",
                chat_id="42",
                max_iterations=5,
                timeout_seconds=10,
            )
        ).task_id

        assert task_id == "bg-m1"
        # El padre puede seguir su turno inmediatamente; la cola hace el resto
        # en background. Le damos al consumer tiempo para completar.
        await asyncio.wait_for(
            _wait_until(lambda: len(historial_persistido) == 1, timeout=5.0),
            timeout=5.0,
        )
    finally:
        await queue.stop()

    # ----- Asserts -----
    # El hijo se ejecutó UNA vez con el prompt original
    target_one_shot.execute.assert_awaited_once()
    kwargs = target_one_shot.execute.await_args.kwargs  # type: ignore[union-attr]
    assert kwargs["task"] == "averiguá el saldo de Galicia"
    assert kwargs["max_iterations"] == 5
    assert kwargs["timeout_seconds"] == 10

    # El dispatcher invocó run_agent.execute del PADRE con marker correcto
    assert caller.run_agent.execute.await_count == 1
    dispatched_kwargs = caller.run_agent.execute.await_args.kwargs  # type: ignore[union-attr]
    dispatched_prompt = caller.run_agent.execute.await_args.args[0]  # type: ignore[union-attr]
    assert dispatched_prompt == "[bg-m1] success: Saldo $5.420"
    assert dispatched_kwargs["channel"] == "telegram"
    assert dispatched_kwargs["chat_id"] == "42"

    # El "historial" recibió el mensaje como Role.USER (REQ-DG-11)
    assert historial_persistido == [(Role.USER, "[bg-m1] success: Saldo $5.420")]


async def test_end_to_end_dispatch_uses_lock_per_scope() -> None:
    """Triangulación: con dos delegaciones al MISMO scope, los dispatches al
    padre se serializan en orden FIFO — el lock-per-scope (REQ-BGD-6)
    garantiza que sus appends al historial no se intercalan.
    """
    historial_persistido: list[str] = []

    async def caller_execute(prompt, **_kw) -> str:
        # Sleep dentro del lock — sin lock, dos dispatches concurrentes
        # intercalarían las appends.
        historial_persistido.append(f"start:{prompt}")
        await asyncio.sleep(0.02)
        historial_persistido.append(f"end:{prompt}")
        return "ok"

    caller = MagicMock()
    caller.run_agent.execute = AsyncMock(side_effect=caller_execute)

    target_one_shot = MagicMock()
    target_one_shot.execute = AsyncMock(return_value="X")

    dispatcher = LLMDispatcherAdapter({"inaki": caller})
    queue = BackgroundDelegationQueueAdapter(
        dispatcher=dispatcher,
        one_shot_resolver=lambda _c, _t: target_one_shot,
        max_concurrent=3,
    )

    await queue.start()
    try:
        # Dos delegaciones al MISMO scope, encoladas back-to-back
        await queue.enqueue(
            caller_agent_id="inaki",
            target_agent_id="r",
            prompt="a",
            system_prompt=None,
            channel="telegram",
            chat_id="42",
            max_iterations=5,
            timeout_seconds=10,
        )
        await queue.enqueue(
            caller_agent_id="inaki",
            target_agent_id="r",
            prompt="b",
            system_prompt=None,
            channel="telegram",
            chat_id="42",
            max_iterations=5,
            timeout_seconds=10,
        )
        await asyncio.wait_for(
            _wait_until(lambda: len(historial_persistido) == 4, timeout=5.0),
            timeout=5.0,
        )
    finally:
        await queue.stop()

    # Cada par (start, end) corresponde al mismo prompt — no hay intercalado.
    primer_prompt = historial_persistido[0].split(":", 1)[1]
    segundo_prompt = historial_persistido[2].split(":", 1)[1]
    assert historial_persistido[0] == f"start:{primer_prompt}"
    assert historial_persistido[1] == f"end:{primer_prompt}"
    assert historial_persistido[2] == f"start:{segundo_prompt}"
    assert historial_persistido[3] == f"end:{segundo_prompt}"
    assert primer_prompt != segundo_prompt


async def _wait_until(predicate, *, timeout: float, interval: float = 0.02) -> None:
    """Polling util para condiciones async. Hace fallar el test si timeout vence."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError(f"_wait_until timed out después de {timeout}s")


async def test_el_hijo_de_una_delegacion_background_no_ve_delegate_ni_background_tasks() -> None:
    """REQ-BGI-5: con el one-shot REAL y las tools del padre en el registro, el schema que
    recibe el LLM del hijo no trae ``delegate`` ni ``background_tasks`` (incluida su acción
    ``inspect``): el hijo no puede mirar ni lanzar delegaciones, solo el padre.

    El schema de ``background_tasks`` que se arma acá es el REAL (``BackgroundTasksTool``),
    así que si alguien lo renombrara sin tocar la exclusión, este test lo cazaría.
    """
    from inaki.agents.delegation.background_tasks_tool import BackgroundTasksTool
    from inaki.kernel.domain.agent_settings import OneShotSettings
    from inaki.kernel.domain.llm_response import LLMResponse
    from inaki.kernel.run_agent_one_shot import RunAgentOneShotUseCase

    herramienta_real = BackgroundTasksTool(queue=MagicMock(), caller_agent_id="inaki")
    schemas_del_padre = [
        {"type": "function", "function": {"name": "read_file", "description": "lee"}},
        {"type": "function", "function": {"name": "delegate", "description": "delega"}},
        {
            "type": "function",
            "function": {
                "name": herramienta_real.name,
                "description": herramienta_real.description,
                "parameters": herramienta_real.parameters_schema,
            },
        },
    ]
    tools = MagicMock()
    tools.get_schemas.return_value = schemas_del_padre
    tools.requires_operator.return_value = False

    llm = AsyncMock()
    llm.complete.return_value = LLMResponse.of_text(
        '```json\n{"status": "success", "summary": "ok", "details": null, "reason": null}\n```'
    )
    one_shot = RunAgentOneShotUseCase(
        llm=llm,
        tools=tools,
        settings=OneShotSettings(agent_id="researcher", system_prompt="Sos el hijo."),
    )

    caller = MagicMock()
    caller.run_agent.execute = AsyncMock(return_value="acuse")
    queue = BackgroundDelegationQueueAdapter(
        dispatcher=LLMDispatcherAdapter({"inaki": caller}),
        one_shot_resolver=lambda _c, _t: one_shot,
        max_concurrent=1,
    )
    await queue.start()
    try:
        await queue.enqueue(
            caller_agent_id="inaki",
            target_agent_id="researcher",
            prompt="investigá",
            system_prompt=None,
            channel="telegram",
            chat_id="42",
            max_iterations=3,
            timeout_seconds=10,
        )
        await asyncio.wait_for(
            _wait_until(lambda: caller.run_agent.execute.await_count == 1, timeout=5.0),
            timeout=5.0,
        )
    finally:
        await queue.stop()

    visto_por_el_hijo = [
        s["function"]["name"]
        for s in llm.complete.await_args.kwargs["tools"]  # type: ignore[union-attr]
    ]
    assert visto_por_el_hijo == ["read_file"]
    # Precondición: el schema que se le ofrecía al padre SÍ tenía ambas, con ``inspect``.
    acciones = herramienta_real.parameters_schema["properties"]["action"]["enum"]
    assert "inspect" in acciones
    nombres_del_padre = {s["function"]["name"] for s in schemas_del_padre}  # type: ignore[index]
    assert {"delegate", "background_tasks"} <= nombres_del_padre
