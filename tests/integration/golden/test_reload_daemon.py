"""Camino dorado 3: el loop del daemon recarga in-place y apaga limpio.

Protege ``run_daemon`` (fase 5 lo mueve a ``app/``, fase 9 lo vuelve genérico
sobre ``IChannel``): un reload cierra la iteración, re-bootstrappea y vuelve a
levantar; SIGTERM termina el loop con ``shutdown()`` ejecutado.

Los canales se sustituyen por tareas que nunca terminan (lo único que el runner
necesita de ellas es poder cancelarlas).
"""

from __future__ import annotations

import asyncio
import os
import signal
from unittest.mock import AsyncMock, MagicMock

import pytest

from infrastructure.daemon_reloader import DaemonReloader


def _container_falso(nombre: str) -> MagicMock:
    container = MagicMock(name=nombre)
    container.reloader = DaemonReloader()
    container.startup = AsyncMock()
    container.shutdown = AsyncMock()
    return container


async def _canal_eterno() -> None:
    await asyncio.get_running_loop().create_future()


async def test_reload_rebootstrappea_y_sigterm_apaga(monkeypatch: pytest.MonkeyPatch) -> None:
    from inaki import daemon_runner

    inicial = _container_falso("inicial")
    recargado = _container_falso("recargado")
    registry = MagicMock()
    bootstrap_fn = MagicMock(return_value=(recargado, registry))

    def _tareas_falsas(app_container, _registry):
        tarea = asyncio.create_task(_canal_eterno(), name=f"canal:{app_container._mock_name}")
        return [tarea], []

    monkeypatch.setattr(daemon_runner, "_build_channel_tasks", _tareas_falsas)

    async def _operador() -> None:
        # Espera a que la primera iteración esté arriba, pide reload, espera a
        # que la segunda arranque y entonces manda SIGTERM.
        while inicial.startup.await_count == 0:
            await asyncio.sleep(0.01)
        inicial.reloader.request_reload()
        while recargado.startup.await_count == 0:
            await asyncio.sleep(0.01)
        os.kill(os.getpid(), signal.SIGTERM)

    operador = asyncio.create_task(_operador())
    await asyncio.wait_for(
        daemon_runner.run_daemon(bootstrap_fn, initial=(inicial, registry)), timeout=5
    )
    await operador

    bootstrap_fn.assert_called_once()
    inicial.startup.assert_awaited_once()
    inicial.shutdown.assert_awaited_once()
    recargado.startup.assert_awaited_once()
    recargado.shutdown.assert_awaited_once()
