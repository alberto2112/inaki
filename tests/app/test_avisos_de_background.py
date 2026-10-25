"""Hook ``al_arrancar`` de la cola de background: avisa lo que el reinicio cortó.

Se COMPONE con el de ``config set`` (``avisos_post_arranque``) y nunca lo reemplaza. El
runner lo espera ANTES de levantar las tareas de los canales, así que lo único que puede
hacer es programar el aviso y volver: el aviso real dispara un turno del LLM padre y
esperarlo dejaría al bot sordo.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from inaki.agents.delegation.background_queue import BackgroundDelegationQueueAdapter
from inaki.agents.delegation.sqlite_task_store import SQLiteBackgroundTaskStore
from inaki.app import bootstrap as bootstrap_mod
from inaki.app.bootstrap import avisos_de_background, componer_al_arrancar
from inaki.kernel.domain.background_task import BackgroundTask


async def test_el_hook_solo_programa_el_aviso_de_la_cola() -> None:
    runtime = MagicMock()

    await avisos_de_background()(runtime)

    runtime.background_queue.programar_avisos_post_arranque.assert_called_once_with()


async def test_con_la_cola_real_el_hook_vuelve_enseguida_aunque_el_padre_tarde(
    tmp_path: Path,
) -> None:
    """El dispatch del aviso (un turno del LLM) puede tardar minutos: el hook NO lo espera."""
    store = SQLiteBackgroundTaskStore(tmp_path / "bg.db")
    await store.open()
    task_id = await store.create(
        BackgroundTask(
            id="",
            caller_agent_id="inaki",
            target_agent_id="r",
            prompt="investigá X",
            system_prompt=None,
            channel="telegram",
            chat_id="42",
            max_iterations=5,
            timeout_seconds=30,
            started_at=datetime.now(timezone.utc),
            status="queued",
        )
    )
    await store.update(task_id, status="running", run_started_at=datetime.now(timezone.utc))
    dispatcher = MagicMock()
    turno_colgado = asyncio.Event()

    async def dispatch_lento(**_kw):
        await turno_colgado.wait()  # el padre "piensa" para siempre
        return ""

    dispatcher.dispatch = AsyncMock(side_effect=dispatch_lento)
    cola = BackgroundDelegationQueueAdapter(
        dispatcher=dispatcher, one_shot_resolver=lambda _c, _t: None, store=store
    )
    await cola.start()  # el barrido deja la task `interrupted` sin avisar
    runtime = MagicMock(background_queue=cola)
    try:
        await asyncio.wait_for(avisos_de_background()(runtime), timeout=1.0)
        await asyncio.sleep(0.05)  # deja arrancar la tarea del aviso
        # El hook ya volvió y el aviso sigue en vuelo, esperando al padre.
        dispatcher.dispatch.assert_awaited_once()
        assert cola._aviso_task is not None and not cola._aviso_task.done()
    finally:
        await cola.stop()  # corta el aviso en vuelo


async def test_componer_corre_los_hooks_en_orden() -> None:
    orden: list[str] = []

    def hook(nombre: str):
        async def corre(_runtime) -> None:
            orden.append(nombre)

        return corre

    await componer_al_arrancar(hook("config"), hook("background"))(MagicMock())

    assert orden == ["config", "background"]


async def test_si_el_primero_falla_el_segundo_corre_igual(caplog: pytest.LogCaptureFixture) -> None:
    """Un aviso de config roto no puede dejar sin aviso a las delegaciones cortadas."""
    corrio: list[str] = []

    async def roto(_runtime) -> None:
        raise RuntimeError("el aviso de config reventó")

    async def sano(_runtime) -> None:
        corrio.append("background")

    with caplog.at_level(logging.ERROR):
        await componer_al_arrancar(roto, sano)(MagicMock())

    assert corrio == ["background"]
    assert "el aviso de config reventó" in caplog.text


async def test_cada_hook_recibe_el_runtime() -> None:
    runtime = MagicMock()
    visto: list[object] = []

    async def hook(r) -> None:
        visto.append(r)

    await componer_al_arrancar(hook, hook)(runtime)

    assert visto == [runtime, runtime]


def test_run_daemon_mode_pasa_al_runner_los_dos_avisos_compuestos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El runner recibe UN hook que corre el aviso de config Y el de background."""
    monkeypatch.setattr("inaki.config.ensure_user_channel_dirs", lambda *_a, **_k: None)
    monkeypatch.setattr("inaki.app.assembly.ensamblar", lambda *_a, **_k: MagicMock())
    capturado: dict = {}

    async def _run_daemon(bootstrap_fn, initial, **kwargs):
        capturado["al_arrancar"] = kwargs["al_arrancar"]

    monkeypatch.setattr("inaki.app.runner.run_daemon", _run_daemon)

    bootstrap_mod.run_daemon_mode(Path("/cfg"), Path("/cfg/agents"), MagicMock(), MagicMock())

    runtime = MagicMock()
    runtime.get_agent.side_effect = AssertionError("no hay avisos de config pendientes")
    asyncio.run(capturado["al_arrancar"](runtime))

    runtime.background_queue.programar_avisos_post_arranque.assert_called_once_with()
