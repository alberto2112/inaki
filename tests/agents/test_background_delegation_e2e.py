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
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from inaki.agents.delegation.background_queue import (
    BackgroundDelegationQueueAdapter,
)
from inaki.agents.delegation.delegate_tool import DelegateTool
from inaki.agents.delegation.sqlite_task_store import SQLiteBackgroundTaskStore
from inaki.agents.dispatcher import LLMDispatcherAdapter
from inaki.kernel.domain.agent_settings import OneShotSettings
from inaki.kernel.domain.background_task import BackgroundTask
from inaki.kernel.domain.llm_response import LLMResponse
from inaki.kernel.ports.llm_port import ILLMProvider
from inaki.kernel.ports.tool_port import ToolResult
from inaki.kernel.run_agent_one_shot import RunAgentOneShotUseCase
from inaki.shared.message import Message, Role


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


# ---------------------------------------------------------------------------
# delegation-resume 6.1 — pausa → resume → success por la cadena REAL
#
# DelegateTool real → cola real → RunAgentOneShotUseCase real (tool loop incluido) → store
# SQLite real en ``tmp_path``. Lo único guionado es el LLM del hijo (``LLMGuionado``) y el
# dispatcher hacia el padre (``PadreRegistrador``). Los tests de la tool mockean la cola y los
# de la cola no pasan por la tool: este es el único lugar donde se tocan las dos.
# ---------------------------------------------------------------------------

_PREGUNTA = "¿Con qué cuenta hago el cobro?"
_RESPUESTA = "Con la cuenta B."
_SALIDA_ENORME = "linea de salida\n" * 1_250  # 20.000 chars: pasa el cap de 12.450 por mensaje


class LLMGuionado(ILLMProvider):
    """LLM del hijo: devuelve las respuestas en orden y guarda lo que recibió en cada llamada.

    La lista que le pasa el tool loop se MUTA después de cada llamada, así que se guarda una
    copia: lo que se asserta es lo que el modelo vio en ESE momento.
    """

    def __init__(self, *respuestas: LLMResponse) -> None:
        self._respuestas = list(respuestas)
        self.llamadas: list[list[Message]] = []

    async def complete(
        self,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        self.llamadas.append(list(messages))
        assert self._respuestas, "el LLM guionado se quedó sin respuestas"
        return self._respuestas.pop(0)

    async def stream(self, messages: list[Message], system_prompt: str) -> AsyncIterator[str]:
        raise NotImplementedError
        yield ""  # pragma: no cover


class PadreRegistrador:
    """Dispatcher falso del padre: registra cada ``[bg-N] ...`` y avisa por evento."""

    def __init__(self) -> None:
        self.llamadas: list[dict] = []
        self._llegadas = [asyncio.Event() for _ in range(8)]

    async def dispatch(self, **kw) -> str:
        self.llamadas.append(kw)
        self._llegadas[len(self.llamadas) - 1].set()
        return ""

    async def llego(self, n: int) -> None:
        """Espera (con tope) a que haya al menos ``n`` mensajes entregados al padre."""
        await asyncio.wait_for(self._llegadas[n - 1].wait(), timeout=5.0)

    @property
    def prompts(self) -> list[str]:
        return [kw["prompt"] for kw in self.llamadas]


def _llamada_a_tool(call_id: str) -> dict:
    return {"id": call_id, "function": {"name": "shell_exec", "arguments": '{"command": "ls"}'}}


def _ir_a_la_tool(call_id: str) -> LLMResponse:
    return LLMResponse(text_blocks=[], tool_calls=[_llamada_a_tool(call_id)])


def _cierre(status: str, resumen: str, *, pregunta: str | None = None) -> LLMResponse:
    bloque = {
        "status": status,
        "summary": resumen,
        "details": None,
        "reason": None,
        "question": pregunta,
    }
    return LLMResponse.of_text("```json\n" + json.dumps(bloque, ensure_ascii=False) + "\n```")


def _pausa(pregunta: str = _PREGUNTA) -> LLMResponse:
    return _cierre("needs_input", "Necesito un dato", pregunta=pregunta)


@dataclass
class Montaje:
    queue: BackgroundDelegationQueueAdapter
    tool: DelegateTool
    padre: PadreRegistrador
    llm: LLMGuionado
    store: SQLiteBackgroundTaskStore
    ejecutor: MagicMock


def _montar(
    db: Path, llm: LLMGuionado, *, max_iterations: int = 10, salida_tool: str = _SALIDA_ENORME
) -> Montaje:
    """Arma la cadena completa sobre ``db``. Llamarla otra vez con la misma ruta = un reinicio."""
    ejecutor = MagicMock()
    ejecutor.get_schemas.return_value = []
    ejecutor.requires_operator.return_value = False
    ejecutor.execute = AsyncMock(
        return_value=ToolResult(tool_name="shell_exec", output=salida_tool, success=True)
    )
    hijo = RunAgentOneShotUseCase(
        llm=llm,
        tools=ejecutor,
        settings=OneShotSettings(
            agent_id="r", system_prompt="Sos el hijo.", request_delay_seconds=0.0
        ),
    )
    store = SQLiteBackgroundTaskStore(db)
    padre = PadreRegistrador()
    queue = BackgroundDelegationQueueAdapter(
        dispatcher=padre,  # type: ignore[arg-type]
        one_shot_resolver=lambda _caller, target: hijo if target == "r" else None,
        max_concurrent=2,
        store=store,
    )
    contenedor = MagicMock()
    contenedor.get_turn_scope = MagicMock(return_value=("telegram", "42"))
    tool = DelegateTool(
        allowed_targets=["r"],
        build_child=MagicMock(return_value=None),
        max_iterations_per_sub=max_iterations,
        timeout_seconds=30,
        caller_agent_id="inaki",
        caller_container=contenedor,
        queue=queue,
    )
    return Montaje(queue, tool, padre, llm, store, ejecutor)


async def _delegar(m: Montaje) -> str:
    """``delegate(wait=false)`` como lo haría el padre: devuelve el ``bg-N`` encolado."""
    r = await m.tool.execute(agent_id="r", task="cobrá la factura 77", wait=False)
    assert r.success, r.output
    return json.loads(r.output)["details"].splitlines()[0]


async def _reanudar(m: Montaje, task_id: str, respuesta: str = _RESPUESTA) -> dict:
    r = await m.tool.execute(resume=task_id, task=respuesta)
    return json.loads(r.output)


async def _fila(m: Montaje, task_id: str) -> BackgroundTask:
    """La fila durable de ``task_id`` (el test falla si no existe)."""
    fila = await m.store.get(task_id, "inaki")
    assert fila is not None, f"{task_id} no está en el store"
    return fila


async def _hasta_terminada(m: Montaje) -> None:
    """Espera a que la cola no tenga nada vivo del caller (terminal: ``delivered``)."""
    await _wait_until(lambda: not m.queue.snapshot_inflight("inaki"), timeout=5.0)


async def _hasta_notificada(m: Montaje, task_id: str) -> None:
    """Espera a que el aviso de la pausa se haya entregado Y marcado (``notified``)."""
    for _ in range(500):
        if (await _fila(m, task_id)).notified:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("la pausa nunca quedó marcada como notificada")


def _roles(mensajes: list[Message]) -> list[Role]:
    return [msg.role for msg in mensajes]


async def test_e2e_pausa_resume_y_success_con_el_transcript_completo(tmp_path: Path) -> None:
    """Success Criteria 1: el hijo pausa, el padre contesta con ``delegate(resume=)`` y el hijo
    continúa viendo TODO lo que había hecho, incluida una salida de tool más larga que el cap."""
    llm = LLMGuionado(
        _ir_a_la_tool("c1"),
        _pausa(),
        _cierre("success", "Cobrado con la cuenta B."),
    )
    m = _montar(tmp_path / "bg.db", llm)
    await m.queue.start()
    try:
        task_id = await _delegar(m)
        await m.padre.llego(1)
        await _hasta_notificada(m, task_id)

        # El padre recibió la pregunta, con las dos vías de continuar (REQ-RES-15).
        assert m.padre.prompts[0].startswith(f"[{task_id}] needs_input: {_PREGUNTA}")
        assert f'delegate(resume="{task_id}"' in m.padre.prompts[0]
        pausada = await _fila(m, task_id)
        assert pausada.status == "awaiting_input" and pausada.notified is True

        resultado = await _reanudar(m, task_id)
        assert resultado["status"] == "success" and "Delegation resumed" in resultado["summary"]
        assert resultado["details"].startswith(f"{task_id} continues")
        assert "8 iterations left" in resultado["details"]  # max 10 - 2 usadas en el tramo 1

        await m.padre.llego(2)
        await _hasta_terminada(m)
        final = await _fila(m, task_id)
    finally:
        await m.queue.stop()

    # El tramo 2 vio el historial entero + la respuesta como último USER (3ª llamada al LLM).
    assert len(llm.llamadas) == 3
    visto = llm.llamadas[2]
    assert _roles(visto) == [Role.USER, Role.ASSISTANT, Role.TOOL, Role.ASSISTANT, Role.USER]
    assert visto[0].content == "cobrá la factura 77"
    assert visto[1].tool_calls and visto[1].tool_calls[0]["id"] == "c1"
    # La salida de 20.000 chars llega ENTERA, no recortada a 12.450: la reparó el snapshot.
    assert visto[2].tool_call_id == "c1" and visto[2].content == _SALIDA_ENORME
    assert '"status": "needs_input"' in visto[3].content
    assert visto[4].content == _RESPUESTA

    # Misma fila, mismo id: ahora entregada con el resultado del tramo 2.
    assert final.id == task_id and final.status == "delivered"
    assert m.padre.prompts[1].startswith(f"[{task_id}] success")
    assert "Cobrado con la cuenta B." in m.padre.prompts[1]
    # El tool se ejecutó una sola vez: el tramo 2 no repite el trabajo del tramo 1.
    assert m.ejecutor.execute.await_count == 1

    # El transcript durable es append-only y continuo: 4 del tramo 1 + 2 del tramo 2.
    store2 = SQLiteBackgroundTaskStore(tmp_path / "bg.db")
    await store2.open()
    try:
        pasos, total = await store2.messages(task_id, last_n=50)
    finally:
        await store2.close()
    assert total == 6 and [p.seq for p in pasos] == [1, 2, 3, 4, 5, 6]
    assert pasos[2].content == _SALIDA_ENORME and pasos[2].original_chars is None
    assert pasos[4].role is Role.USER and pasos[4].content == _RESPUESTA
    assert final.iterations_used == 3  # tool + pausa + cierre del tramo 2


async def test_e2e_el_resume_funciona_despues_de_un_reinicio(tmp_path: Path) -> None:
    """La pausa vive en el disco: tras ``stop()`` y una cola NUEVA sobre la misma DB, la fila se
    rehidrata con su pregunta, no se re-avisa (ya estaba notificada) y el resume sigue andando."""
    db = tmp_path / "bg.db"
    vida1 = _montar(db, LLMGuionado(_ir_a_la_tool("c1"), _pausa()))
    await vida1.queue.start()
    try:
        task_id = await _delegar(vida1)
        await vida1.padre.llego(1)
        await _hasta_notificada(vida1, task_id)
    finally:
        await vida1.queue.stop()

    llm2 = LLMGuionado(_cierre("success", "Cobrado tras el reinicio."))
    vida2 = _montar(db, llm2)
    await vida2.queue.start()
    try:
        # Rehidratada: sigue pausada y con su pregunta a la vista del padre (REQ-RES-4 S1).
        vistas = vida2.queue.snapshot_inflight("inaki")
        assert [(v.id, v.status, v.question_preview) for v in vistas] == [
            (task_id, "awaiting_input", _PREGUNTA)
        ]
        vida2.queue.programar_avisos_post_arranque()
        assert vida2.queue._aviso_task is not None
        await asyncio.wait_for(asyncio.shield(vida2.queue._aviso_task), timeout=5.0)
        assert vida2.padre.llamadas == []  # ya estaba notificada: sin re-aviso

        resultado = await _reanudar(vida2, task_id)
        assert resultado["status"] == "success", resultado
        await vida2.padre.llego(1)
        await _hasta_terminada(vida2)
        final = await _fila(vida2, task_id)
    finally:
        await vida2.queue.stop()

    # El hijo de la vida 2 vio el transcript de la vida 1 más la respuesta, con la salida
    # grande intacta.
    visto = llm2.llamadas[0]
    assert _roles(visto) == [Role.USER, Role.ASSISTANT, Role.TOOL, Role.ASSISTANT, Role.USER]
    assert visto[2].content == _SALIDA_ENORME and visto[4].content == _RESPUESTA
    assert vida2.padre.prompts[0].startswith(f"[{task_id}] success")
    assert final.status == "delivered"


async def test_e2e_una_segunda_pausa_en_el_tramo_2_se_vuelve_a_reanudar(tmp_path: Path) -> None:
    """Tres tramos: pausa, resume, nueva pausa (con otra salida enorme), resume, success. El
    snapshot del tramo 2 repara SOLO sus filas y el tramo 3 ve los dos tramos completos."""
    llm = LLMGuionado(
        _ir_a_la_tool("c1"),
        _pausa("¿Qué cuenta?"),
        _ir_a_la_tool("c2"),
        _pausa("¿Y qué monto?"),
        _cierre("success", "Cobrado 500 con la cuenta B."),
    )
    m = _montar(tmp_path / "bg.db", llm)
    await m.queue.start()
    try:
        task_id = await _delegar(m)
        await m.padre.llego(1)
        await _hasta_notificada(m, task_id)
        assert (await _reanudar(m, task_id, "La B."))["status"] == "success"

        # Segunda pausa: otro mensaje al padre y la misma fila de vuelta en awaiting_input.
        await m.padre.llego(2)
        await _hasta_notificada(m, task_id)
        assert m.padre.prompts[1].startswith(f"[{task_id}] needs_input: ¿Y qué monto?")
        segunda = await _fila(m, task_id)
        assert segunda.status == "awaiting_input" and segunda.iterations_used == 4

        assert (await _reanudar(m, task_id, "500."))["status"] == "success"
        await m.padre.llego(3)
        await _hasta_terminada(m)
        final = await _fila(m, task_id)
    finally:
        await m.queue.stop()

    # Tramo 3: los dos tramos previos completos (USER, A+T+A, USER, A+T+A) + la respuesta.
    visto = llm.llamadas[4]
    assert _roles(visto) == [
        Role.USER, Role.ASSISTANT, Role.TOOL, Role.ASSISTANT,
        Role.USER, Role.ASSISTANT, Role.TOOL, Role.ASSISTANT,
        Role.USER,
    ]  # fmt: skip
    assert visto[2].content == _SALIDA_ENORME and visto[6].content == _SALIDA_ENORME
    assert [visto[4].content, visto[8].content] == ["La B.", "500."]
    assert m.padre.prompts[2].startswith(f"[{task_id}] success")
    assert final.status == "delivered" and final.iterations_used == 5

    store2 = SQLiteBackgroundTaskStore(tmp_path / "bg.db")
    await store2.open()
    try:
        pasos, total = await store2.messages(task_id, last_n=50)
    finally:
        await store2.close()
    assert total == 10 and [p.seq for p in pasos] == list(range(1, 11))
    assert all(p.original_chars is None for p in pasos)


async def test_e2e_el_presupuesto_es_acumulado_y_agotado_no_se_puede_pausar_otra_vez(
    tmp_path: Path,
) -> None:
    """Techo de iteraciones: con ``max_iterations=3`` el tramo 1 gasta 2 (tool + pausa), el
    tramo 2 recibe 1 y su cierre ``needs_input`` ya no encuentra presupuesto: la task termina
    ``delivered`` no reanudable. En total el modelo habló 3 veces, nunca más que sin pausas."""
    llm = LLMGuionado(
        _ir_a_la_tool("c1"),
        _pausa("¿Qué cuenta?"),
        _pausa("¿Y ahora qué monto?"),
    )
    m = _montar(tmp_path / "bg.db", llm, max_iterations=3)
    await m.queue.start()
    try:
        task_id = await _delegar(m)
        await m.padre.llego(1)
        await _hasta_notificada(m, task_id)
        assert (await _reanudar(m, task_id))["status"] == "success"

        await m.padre.llego(2)
        await _hasta_terminada(m)
        final = await _fila(m, task_id)
        intento = await _reanudar(m, task_id, "otra vez")
    finally:
        await m.queue.stop()

    assert final.status == "delivered" and final.result_status == "needs_input"
    assert "NOT resumable" in m.padre.prompts[1] and "¿Y ahora qué monto?" in m.padre.prompts[1]
    assert len(llm.llamadas) == 3  # techo total = max_iterations (3), sin llamadas de más
    # Y el padre no puede reanudar lo que no está pausado.
    assert intento["status"] == "failed" and intento["reason"] == "not_awaiting"
