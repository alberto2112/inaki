"""Unit tests para BackgroundDelegationQueueAdapter (REQ-BGD-1..8 + REQ-DG-11).

El adapter es la implementación in-memory de IBackgroundDelegationQueue:
- enqueue: registra task, devuelve bg-N en <50ms (REQ-BGD-2)
- snapshot_inflight: lista tasks queued/running del caller (REQ-BGD-4)
- start/stop: ciclo del consumer (REQ-BGD-1, 8)
- consumer: ejecuta one-shot bajo Semaphore(3) + dispatch con marker (REQ-BGD-3,5)
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

from adapters.outbound.delegation.background_queue_adapter import (
    BackgroundDelegationQueueAdapter,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_adapter(
    *,
    one_shot_for: dict | None = None,
    dispatcher: MagicMock | None = None,
    max_concurrent: int = 3,
    timeout_seconds: int = 30,
    max_iterations: int = 5,
) -> tuple[BackgroundDelegationQueueAdapter, MagicMock]:
    """Construye un adapter con dispatcher mockeado.

    ``one_shot_for`` es un dict ``{target_agent_id: one_shot_use_case_mock}`` que
    se resuelve via callable. Si una key no existe, el resolver devuelve None
    (simulando un target desconocido).
    """
    one_shot_for = one_shot_for or {}
    dispatcher = dispatcher or MagicMock()
    dispatcher.dispatch = AsyncMock(return_value="")

    def resolver(target_id: str):
        return one_shot_for.get(target_id)

    adapter = BackgroundDelegationQueueAdapter(
        dispatcher=dispatcher,
        one_shot_resolver=resolver,
        max_iterations_per_sub=max_iterations,
        timeout_seconds=timeout_seconds,
        max_concurrent=max_concurrent,
    )
    return adapter, dispatcher


# ---------------------------------------------------------------------------
# 2.1 — enqueue + snapshot_inflight
# ---------------------------------------------------------------------------


class TestEnqueue:
    async def test_enqueue_devuelve_bg_1_la_primera_vez(self) -> None:
        adapter, _ = _build_adapter()

        task_id = await adapter.enqueue(
            caller_agent_id="inaki",
            target_agent_id="researcher",
            prompt="investigá X",
            system_prompt=None,
            channel="telegram",
            chat_id="42",
        )

        assert task_id == "bg-1"

    async def test_enqueue_counter_monotonico(self) -> None:
        """Triangulación: cada llamada incrementa el counter (REQ-BGD-2)."""
        adapter, _ = _build_adapter()

        id1 = await adapter.enqueue(
            caller_agent_id="inaki", target_agent_id="r", prompt="a",
            system_prompt=None, channel="", chat_id="",
        )
        id2 = await adapter.enqueue(
            caller_agent_id="inaki", target_agent_id="r", prompt="b",
            system_prompt=None, channel="", chat_id="",
        )
        id3 = await adapter.enqueue(
            caller_agent_id="otro", target_agent_id="r", prompt="c",
            system_prompt=None, channel="", chat_id="",
        )

        assert (id1, id2, id3) == ("bg-1", "bg-2", "bg-3")

    async def test_enqueue_completa_en_menos_de_50ms(self) -> None:
        """REQ-BGD-2: latencia <50ms (no espera al hijo)."""
        adapter, _ = _build_adapter()

        inicio = time.perf_counter()
        await adapter.enqueue(
            caller_agent_id="inaki", target_agent_id="r", prompt="x",
            system_prompt=None, channel="", chat_id="",
        )
        elapsed = time.perf_counter() - inicio

        assert elapsed < 0.050, f"enqueue tardó {elapsed * 1000:.2f}ms (>50ms)"


class TestSnapshotInflight:
    async def test_snapshot_vacio_inicialmente(self) -> None:
        adapter, _ = _build_adapter()

        assert adapter.snapshot_inflight("inaki") == []

    async def test_snapshot_contiene_task_recien_encolada(self) -> None:
        adapter, _ = _build_adapter()

        task_id = await adapter.enqueue(
            caller_agent_id="inaki", target_agent_id="researcher",
            prompt="investigá el saldo de Galicia",
            system_prompt=None, channel="telegram", chat_id="42",
        )

        snap = adapter.snapshot_inflight("inaki")
        assert len(snap) == 1
        assert snap[0].id == task_id
        assert snap[0].target_agent_id == "researcher"
        assert snap[0].status in {"queued", "running"}
        assert "investigá el saldo de Galicia" in snap[0].prompt_preview

    async def test_snapshot_aisla_por_caller(self) -> None:
        """Triangulación: snapshot de un caller no incluye tasks de otros."""
        adapter, _ = _build_adapter()

        await adapter.enqueue(
            caller_agent_id="inaki", target_agent_id="r", prompt="a",
            system_prompt=None, channel="", chat_id="",
        )
        await adapter.enqueue(
            caller_agent_id="alberto", target_agent_id="r", prompt="b",
            system_prompt=None, channel="", chat_id="",
        )

        snap_inaki = adapter.snapshot_inflight("inaki")
        snap_alberto = adapter.snapshot_inflight("alberto")
        assert len(snap_inaki) == 1
        assert len(snap_alberto) == 1
        assert snap_inaki[0].id != snap_alberto[0].id

    async def test_snapshot_para_caller_sin_tasks_es_lista_vacia(self) -> None:
        adapter, _ = _build_adapter()

        await adapter.enqueue(
            caller_agent_id="inaki", target_agent_id="r", prompt="a",
            system_prompt=None, channel="", chat_id="",
        )

        assert adapter.snapshot_inflight("otro") == []


# ---------------------------------------------------------------------------
# 2.2 — Lifecycle: start / stop / in-flight abandoned
# ---------------------------------------------------------------------------


def _one_shot_returning(value: str) -> MagicMock:
    """One-shot mock que devuelve ``value`` cuando se invoca .execute(...)."""
    mock = MagicMock()
    mock.execute = AsyncMock(return_value=value)
    return mock


def _one_shot_sleeping(seconds: float, value: str = "done") -> MagicMock:
    """One-shot mock que duerme antes de devolver — para forzar overlap."""
    async def slow(**_kw):
        await asyncio.sleep(seconds)
        return value

    mock = MagicMock()
    mock.execute = AsyncMock(side_effect=slow)
    return mock


class TestLifecycle:
    async def test_start_lanza_consumer_task(self) -> None:
        adapter, _ = _build_adapter()

        await adapter.start()

        assert adapter._consumer_task is not None
        assert not adapter._consumer_task.done()

        await adapter.stop()

    async def test_start_es_idempotente(self) -> None:
        """Segunda llamada a start() no debe crear un consumer adicional."""
        adapter, _ = _build_adapter()

        await adapter.start()
        primer_task = adapter._consumer_task
        await adapter.start()

        assert adapter._consumer_task is primer_task
        await adapter.stop()

    async def test_stop_cancela_consumer(self) -> None:
        adapter, _ = _build_adapter()
        await adapter.start()
        consumer = adapter._consumer_task

        await asyncio.wait_for(adapter.stop(), timeout=5.0)

        assert consumer is not None and consumer.done()
        assert adapter._consumer_task is None

    async def test_stop_antes_de_start_es_no_op(self) -> None:
        adapter, _ = _build_adapter()

        # No debe lanzar
        await adapter.stop()

    async def test_stop_abandona_tasks_in_flight_sin_dispatchar(self) -> None:
        """REQ-BGD-8: stop() durante una task in-flight NO debe dispatchar el
        resultado (la task se abandona)."""
        slow_one_shot = _one_shot_sleeping(10.0)
        adapter, dispatcher = _build_adapter(one_shot_for={"r": slow_one_shot})

        await adapter.start()
        await adapter.enqueue(
            caller_agent_id="inaki", target_agent_id="r", prompt="x",
            system_prompt=None, channel="", chat_id="",
        )
        # Dejar que el consumer la levante y la lance
        await asyncio.sleep(0.05)
        await asyncio.wait_for(adapter.stop(), timeout=2.0)

        dispatcher.dispatch.assert_not_called()


# ---------------------------------------------------------------------------
# 2.3 — Semaphore(3) + FIFO
# ---------------------------------------------------------------------------


class TestSemaphoreYFIFO:
    async def test_cuarta_task_queda_queued_mientras_3_corren(self) -> None:
        """REQ-BGD-3: solo 3 tasks pueden estar ``running`` simultáneamente."""
        slow = _one_shot_sleeping(0.2)
        adapter, _ = _build_adapter(one_shot_for={"r": slow})

        await adapter.start()
        for _ in range(4):
            await adapter.enqueue(
                caller_agent_id="inaki", target_agent_id="r", prompt="x",
                system_prompt=None, channel="", chat_id="",
            )

        # Dar tiempo al consumer a tomar 3 y arrancar la 4ta (queue)
        await asyncio.sleep(0.05)
        snap = adapter.snapshot_inflight("inaki")
        running = [t for t in snap if t.status == "running"]
        queued = [t for t in snap if t.status == "queued"]

        assert len(running) == 3
        assert len(queued) == 1

        await adapter.stop()

    async def test_orden_fifo_de_dispatch(self) -> None:
        """REQ-BGD-3: las tasks completan en el mismo orden que se encolaron."""
        fast = _one_shot_sleeping(0.02)
        adapter, dispatcher = _build_adapter(
            one_shot_for={"r": fast}, max_concurrent=1,  # serializa para orden estricto
        )

        await adapter.start()
        ids = []
        for letra in ["a", "b", "c"]:
            ids.append(await adapter.enqueue(
                caller_agent_id="inaki", target_agent_id="r", prompt=letra,
                system_prompt=None, channel="", chat_id="",
            ))

        # Esperar a que las 3 terminen
        await asyncio.sleep(0.2)
        await adapter.stop()

        # Orden de calls al dispatcher coincide con orden de enqueue
        prompts_dispatchados = [
            call.kwargs["prompt"] for call in dispatcher.dispatch.await_args_list
        ]
        # Cada uno empieza con [bg-N] — verifico orden por N
        assert len(prompts_dispatchados) == 3
        for idx, expected_id in enumerate(ids):
            assert prompts_dispatchados[idx].startswith(f"[{expected_id}] ")


# ---------------------------------------------------------------------------
# 2.4 — Happy path: dispatch con marker + purge tras dispatch exitoso
# ---------------------------------------------------------------------------


class TestHappyPathDispatch:
    async def test_dispatch_invocado_con_marker_y_resultado(self) -> None:
        """REQ-BGD-5 + REQ-DG-11: prompt dispatcheado tiene formato [bg-N] <result>."""
        one_shot = _one_shot_returning("Saldo $5.420")
        adapter, dispatcher = _build_adapter(one_shot_for={"researcher": one_shot})

        await adapter.start()
        task_id = await adapter.enqueue(
            caller_agent_id="inaki", target_agent_id="researcher",
            prompt="dame el saldo", system_prompt=None,
            channel="telegram", chat_id="42",
        )
        await asyncio.sleep(0.05)
        await adapter.stop()

        dispatcher.dispatch.assert_awaited_once_with(
            agent_id="inaki",
            prompt=f"[{task_id}] Saldo $5.420",
            channel="telegram",
            chat_id="42",
        )

    async def test_dispatch_propaga_channel_y_chat_id_originales(self) -> None:
        """Triangulación: el resultado vuelve al scope ORIGINAL, no al default."""
        one_shot = _one_shot_returning("ok")
        adapter, dispatcher = _build_adapter(one_shot_for={"r": one_shot})

        await adapter.start()
        await adapter.enqueue(
            caller_agent_id="alberto", target_agent_id="r", prompt="x",
            system_prompt=None, channel="cli", chat_id="local",
        )
        await asyncio.sleep(0.05)
        await adapter.stop()

        kwargs = dispatcher.dispatch.await_args.kwargs
        assert kwargs["agent_id"] == "alberto"
        assert kwargs["channel"] == "cli"
        assert kwargs["chat_id"] == "local"

    async def test_task_purgada_tras_dispatch_exitoso(self) -> None:
        """FIX silent-death: la task se purga DESPUÉS de un dispatch exitoso.

        En el momento del dispatch la task TODAVÍA está en `_tasks` (aún no se
        sabe si la entrega va a tener éxito); una vez que dispatch retorna sin
        error, la task se elimina. Verificamos ambas mitades.
        """
        one_shot = _one_shot_returning("ok")
        adapter, dispatcher = _build_adapter(one_shot_for={"r": one_shot})

        capturado: dict = {}

        async def captura_y_devuelve(**kw) -> str:
            # Snapshot del estado del adapter EN el momento del dispatch
            capturado["snap"] = list(adapter._tasks.keys())
            return ""

        dispatcher.dispatch.side_effect = captura_y_devuelve

        await adapter.start()
        task_id = await adapter.enqueue(
            caller_agent_id="inaki", target_agent_id="r", prompt="x",
            system_prompt=None, channel="", chat_id="",
        )
        await asyncio.sleep(0.05)
        await adapter.stop()

        # Durante el dispatch la task todavía estaba presente...
        assert task_id in capturado["snap"]
        # ...y tras un dispatch exitoso se purgó.
        assert task_id not in adapter._tasks

    async def test_task_no_purgada_si_dispatch_falla(self) -> None:
        """FIX silent-death: si el dispatch falla en todos los intentos, la task
        NO se purga — queda visible en snapshot para que el agente no la dé por
        perdida ni la relance a ciegas.
        """
        one_shot = _one_shot_returning("ok")
        adapter, dispatcher = _build_adapter(one_shot_for={"r": one_shot})
        # Todos los intentos de dispatch fallan.
        dispatcher.dispatch.side_effect = RuntimeError("dispatch boom")

        await adapter.start()
        task_id = await adapter.enqueue(
            caller_agent_id="inaki", target_agent_id="r", prompt="x",
            system_prompt=None, channel="", chat_id="",
        )
        # Esperar a que se agoten los reintentos (3 intentos con backoff 0.5*n).
        await asyncio.sleep(2.0)
        await adapter.stop()

        # La task sigue viva y aparece en el snapshot del caller.
        assert task_id in adapter._tasks
        snap = adapter.snapshot_inflight("inaki")
        assert any(v.id == task_id for v in snap)

    async def test_dispatch_se_reintenta_y_eventualmente_entrega(self) -> None:
        """FIX silent-death: un fallo transitorio de dispatch se reintenta; si un
        intento posterior tiene éxito, la task se entrega y se purga.
        """
        one_shot = _one_shot_returning("ok")
        adapter, dispatcher = _build_adapter(one_shot_for={"r": one_shot})

        intentos = {"n": 0}

        async def falla_una_vez(**kw) -> str:
            intentos["n"] += 1
            if intentos["n"] == 1:
                raise RuntimeError("transient")
            return ""

        dispatcher.dispatch.side_effect = falla_una_vez

        await adapter.start()
        task_id = await adapter.enqueue(
            caller_agent_id="inaki", target_agent_id="r", prompt="x",
            system_prompt=None, channel="", chat_id="",
        )
        await asyncio.sleep(1.0)
        await adapter.stop()

        assert intentos["n"] >= 2  # hubo al menos un reintento
        assert task_id not in adapter._tasks  # se entregó y purgó


# ---------------------------------------------------------------------------
# 2.5 — Error path: exception → "[bg-N] failed: ..." + purge igual
# ---------------------------------------------------------------------------


class TestErrorPath:
    async def test_exception_en_one_shot_se_serializa_en_el_marker(self) -> None:
        """REQ-BGD-5: cualquier exception del hijo se reporta como `[bg-N] failed: ...`."""
        roto = MagicMock()
        roto.execute = AsyncMock(side_effect=RuntimeError("boom"))
        adapter, dispatcher = _build_adapter(one_shot_for={"r": roto})

        await adapter.start()
        task_id = await adapter.enqueue(
            caller_agent_id="inaki", target_agent_id="r", prompt="x",
            system_prompt=None, channel="cli", chat_id="",
        )
        await asyncio.sleep(0.05)
        await adapter.stop()

        prompt = dispatcher.dispatch.await_args.kwargs["prompt"]
        assert prompt.startswith(f"[{task_id}] failed: RuntimeError")
        assert "boom" in prompt

    async def test_target_desconocido_reporta_unknown_target_agent(self) -> None:
        """Triangulación: si el resolver devuelve None, el adapter no crashea —
        reporta el fallo via dispatch con un mensaje específico."""
        adapter, dispatcher = _build_adapter(one_shot_for={})  # sin targets

        await adapter.start()
        task_id = await adapter.enqueue(
            caller_agent_id="inaki", target_agent_id="fantasma", prompt="x",
            system_prompt=None, channel="", chat_id="",
        )
        await asyncio.sleep(0.05)
        await adapter.stop()

        prompt = dispatcher.dispatch.await_args.kwargs["prompt"]
        assert prompt == f"[{task_id}] failed: unknown_target_agent: 'fantasma'"

    async def test_purga_ocurre_aun_si_one_shot_lanza(self) -> None:
        """El path de error también se purga: el fallo del hijo se serializa en el
        marker `[bg-N] failed: ...`, se dispatcha con éxito (mock) y la task se
        purga igual que en el happy path."""
        roto = MagicMock()
        roto.execute = AsyncMock(side_effect=ValueError("nope"))
        adapter, _ = _build_adapter(one_shot_for={"r": roto})

        await adapter.start()
        task_id = await adapter.enqueue(
            caller_agent_id="inaki", target_agent_id="r", prompt="x",
            system_prompt=None, channel="", chat_id="",
        )
        await asyncio.sleep(0.05)
        await adapter.stop()

        assert task_id not in adapter._tasks
