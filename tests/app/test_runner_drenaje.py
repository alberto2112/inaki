"""Contrato de drenaje: ``IChannel.stop()`` espera los turnos en vuelo y su entrega.

``config_set`` pide el reload en el acto, dentro del turno que lo pidió. Lo que
garantiza que la respuesta del turno llegue ANTES del corte no es un timer: es que el
runner detiene los canales primero y cada ``stop()`` no vuelve hasta que sus turnos
entregaron. Recién después caen ``app_container.shutdown()`` y el re-bootstrap.

Este test fija el contrato del lado del runner con un ``IChannel`` falso que lo
cumple. No acota la versión de python-telegram-bot (decisión R3): si una PTB nueva
dejara de esperar las tasks de ``concurrent_updates``, el que avisa es el test de
Telegram, no un pin.
"""

from __future__ import annotations

import asyncio
import os
import signal
from unittest.mock import AsyncMock, MagicMock

import pytest

from inaki.app.reloader import DaemonReloader
from inaki.kernel.ports.channel_port import IChannel


class CanalConTurnoEnVuelo(IChannel):
    """Cumple el contrato: ``stop()`` espera a que el turno en vuelo entregue su respuesta."""

    name = "falso"

    def __init__(self, eventos: list[str]) -> None:
        self._eventos = eventos
        self._turno: asyncio.Task | None = None

    async def start(self) -> None:
        pass

    def recibir_mensaje(self, demora_s: float) -> None:
        """Un turno que tarda ``demora_s`` en entregar (como un handler de PTB)."""

        async def turno() -> None:
            await asyncio.sleep(demora_s)
            self._eventos.append("turno_entregado")

        self._turno = asyncio.create_task(turno())

    async def stop(self) -> None:
        self._eventos.append("stop_inicio")
        if self._turno is not None:
            await self._turno
        self._eventos.append("stop_fin")


async def _canal_eterno() -> None:
    await asyncio.get_running_loop().create_future()


async def test_el_runner_no_corta_ni_rebootstrappea_hasta_que_el_turno_entrega(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from inaki.app import runner

    eventos: list[str] = []
    canal = CanalConTurnoEnVuelo(eventos)

    inicial = MagicMock(name="inicial")
    inicial.channels = (canal,)
    inicial.reloader = DaemonReloader()
    inicial.startup = AsyncMock()

    async def shutdown() -> None:
        eventos.append("shutdown")

    inicial.shutdown = shutdown

    recargado = MagicMock(name="recargado")
    recargado.channels = ()
    recargado.reloader = DaemonReloader()
    recargado.startup = AsyncMock()
    recargado.shutdown = AsyncMock()

    def bootstrap_fn():
        eventos.append("bootstrap")
        return recargado, MagicMock()

    monkeypatch.setattr(
        runner,
        "_build_channel_tasks",
        lambda c, _r: ([asyncio.create_task(_canal_eterno(), name="canal")], []),
    )

    async def operador() -> None:
        while inicial.startup.await_count == 0:
            await asyncio.sleep(0.01)
        # El turno que pide el cambio sigue en vuelo cuando `config_set` pide el reload.
        canal.recibir_mensaje(demora_s=0.2)
        inicial.reloader.request_reload_para_aplicar()
        while recargado.startup.await_count == 0:
            await asyncio.sleep(0.01)
        os.kill(os.getpid(), signal.SIGTERM)

    tarea = asyncio.create_task(operador())
    await asyncio.wait_for(runner.run_daemon(bootstrap_fn, initial=(inicial, MagicMock())), 5)
    await tarea

    assert eventos == [
        "stop_inicio",
        "turno_entregado",  # la respuesta salió ANTES de que el canal diera por detenido...
        "stop_fin",
        "shutdown",  # ...y antes de apagar el container...
        "bootstrap",  # ...y de reconstruir el daemon.
    ]
