"""``run_daemon(al_arrancar=)``: el hook corre tras ``_start_channels`` en CADA iteración.

Es lo que permite mandar el aviso "volví con el cambio aplicado" cuando los bots ya
están inicializados. El hook nunca corta el arranque: el aviso es un cortesía, el
daemon sano es lo que importa.
"""

from __future__ import annotations

import asyncio
import os
import signal
from unittest.mock import AsyncMock, MagicMock

import pytest

from inaki.app.reloader import DaemonReloader


def _container(nombre: str, eventos: list[str]) -> MagicMock:
    container = MagicMock(name=nombre)
    container.reloader = DaemonReloader()

    async def startup() -> None:
        eventos.append(f"{nombre}:startup")

    container.startup = startup
    container.shutdown = AsyncMock()
    return container


async def _canal_eterno() -> None:
    await asyncio.get_running_loop().create_future()


@pytest.fixture
def runner_falso(monkeypatch: pytest.MonkeyPatch):
    """Sustituye canales y tareas: lo único que el runner necesita es poder cancelarlas."""
    from inaki.app import runner

    eventos: list[str] = []

    async def _start_channels(app_container):
        eventos.append(f"{app_container._mock_name}:canales_arriba")
        return []

    def _tareas(app_container, _registry):
        return [asyncio.create_task(_canal_eterno(), name=f"canal:{app_container._mock_name}")], []

    monkeypatch.setattr(runner, "_start_channels", _start_channels)
    monkeypatch.setattr(runner, "_build_channel_tasks", _tareas)
    return runner, eventos


async def _recargar_una_vez_y_apagar(inicial: MagicMock, recargado: MagicMock, eventos: list[str]):
    while f"{inicial._mock_name}:canales_arriba" not in eventos:
        await asyncio.sleep(0.01)
    inicial.reloader.request_reload()
    while f"{recargado._mock_name}:canales_arriba" not in eventos:
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)  # deja correr el hook de la segunda iteración
    os.kill(os.getpid(), signal.SIGTERM)


async def test_el_hook_corre_despues_de_arrancar_los_canales_en_cada_iteracion(runner_falso):
    runner, eventos = runner_falso
    inicial, recargado = _container("inicial", eventos), _container("recargado", eventos)

    async def al_arrancar(app_container) -> None:
        eventos.append(f"{app_container._mock_name}:hook")

    operador = asyncio.create_task(_recargar_una_vez_y_apagar(inicial, recargado, eventos))
    await asyncio.wait_for(
        runner.run_daemon(
            MagicMock(return_value=(recargado, MagicMock())),
            initial=(inicial, MagicMock()),
            al_arrancar=al_arrancar,
        ),
        timeout=5,
    )
    await operador

    assert eventos == [
        "inicial:startup",
        "inicial:canales_arriba",
        "inicial:hook",
        "recargado:startup",
        "recargado:canales_arriba",
        "recargado:hook",
    ]


async def test_un_hook_que_levanta_no_corta_el_arranque_ni_el_loop(runner_falso, caplog):
    runner, eventos = runner_falso
    inicial, recargado = _container("inicial", eventos), _container("recargado", eventos)
    llamadas: list[str] = []

    async def al_arrancar(app_container) -> None:
        llamadas.append(app_container._mock_name)
        raise RuntimeError("el aviso reventó")

    operador = asyncio.create_task(_recargar_una_vez_y_apagar(inicial, recargado, eventos))
    with caplog.at_level("ERROR"):
        await asyncio.wait_for(
            runner.run_daemon(
                MagicMock(return_value=(recargado, MagicMock())),
                initial=(inicial, MagicMock()),
                al_arrancar=al_arrancar,
            ),
            timeout=5,
        )
    await operador

    # Falló en las dos iteraciones y el daemon siguió: la segunda arrancó y apagó limpio.
    assert llamadas == ["inicial", "recargado"]
    recargado.shutdown.assert_awaited_once()
    assert "el aviso reventó" in caplog.text


async def test_sin_hook_el_runner_se_comporta_como_antes(runner_falso):
    runner, eventos = runner_falso
    inicial, recargado = _container("inicial", eventos), _container("recargado", eventos)

    operador = asyncio.create_task(_recargar_una_vez_y_apagar(inicial, recargado, eventos))
    await asyncio.wait_for(
        runner.run_daemon(
            MagicMock(return_value=(recargado, MagicMock())), initial=(inicial, MagicMock())
        ),
        timeout=5,
    )
    await operador

    assert eventos == [
        "inicial:startup",
        "inicial:canales_arriba",
        "recargado:startup",
        "recargado:canales_arriba",
    ]
