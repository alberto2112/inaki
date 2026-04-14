"""Tests para verificar que run_cli setea y limpia el channel context correctamente."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from core.domain.value_objects.channel_context import ChannelContext


@pytest.fixture
def mock_container():
    """Container mockeado con set_channel_context rastreable."""
    container = MagicMock()
    container.run_agent = AsyncMock()
    container.run_agent.execute = AsyncMock(return_value="respuesta")
    container.consolidate_memory = AsyncMock()
    container.consolidate_memory.execute = AsyncMock(return_value="ok")
    container.run_agent._history = AsyncMock()
    container.run_agent._history.load = AsyncMock(return_value=[])
    container.run_agent._history.clear = AsyncMock()
    container.set_channel_context = MagicMock()
    container.get_channel_context = MagicMock(return_value=None)
    return container


@pytest.fixture
def mock_app(mock_container):
    """AppContainer mockeado que retorna el container de prueba."""
    app = MagicMock()
    app.get_agent.return_value = mock_container
    app.registry.get.return_value = MagicMock(
        name="Test",
        description="desc",
        llm=MagicMock(model="test-model", provider="test"),
    )
    return app


async def test_run_cli_setea_channel_context_cli(mock_app, mock_container):
    """run_cli debe setear channel_type='cli' y user_id='local' al inicio."""
    from adapters.inbound.cli.cli_runner import run_cli

    with patch("builtins.input", side_effect=["hola", "/exit"]):
        await run_cli(mock_app, "default")

    # Verificar que se llamó set_channel_context con ChannelContext correcto
    calls = mock_container.set_channel_context.call_args_list
    assert len(calls) >= 1
    primer_llamado = calls[0]
    ctx = primer_llamado[0][0]
    assert isinstance(ctx, ChannelContext)
    assert ctx.channel_type == "cli"
    assert ctx.user_id == "local"


async def test_run_cli_limpia_channel_context_al_salir(mock_app, mock_container):
    """run_cli debe limpiar el channel context con None al terminar."""
    from adapters.inbound.cli.cli_runner import run_cli

    with patch("builtins.input", side_effect=["/exit"]):
        await run_cli(mock_app, "default")

    calls = mock_container.set_channel_context.call_args_list
    # El último llamado debe ser con None
    ultimo_llamado = calls[-1]
    assert ultimo_llamado == call(None)


async def test_run_cli_limpia_context_al_interrumpir(mock_app, mock_container):
    """run_cli debe limpiar el channel context incluso ante KeyboardInterrupt."""
    from adapters.inbound.cli.cli_runner import run_cli

    with patch("builtins.input", side_effect=KeyboardInterrupt):
        await run_cli(mock_app, "default")

    calls = mock_container.set_channel_context.call_args_list
    ultimo_llamado = calls[-1]
    assert ultimo_llamado == call(None)


async def test_run_cli_channel_context_disponible_durante_loop(mock_app, mock_container):
    """El channel context debe estar seteado antes de que se procese cualquier mensaje."""
    from adapters.inbound.cli.cli_runner import run_cli

    ctx_durante_ejecucion: list[ChannelContext | None] = []

    def capturar_contexto(mensaje: str):
        # Capturamos el contexto en el momento en que execute() es llamado
        ctx = mock_container.get_channel_context()
        ctx_durante_ejecucion.append(ctx)
        return "respuesta"

    mock_container.run_agent.execute = AsyncMock(side_effect=capturar_contexto)
    mock_container.get_channel_context.return_value = ChannelContext(
        channel_type="cli", user_id="local"
    )

    with patch("builtins.input", side_effect=["mensaje de prueba", "/exit"]):
        await run_cli(mock_app, "default")

    # El contexto fue consultado durante la ejecución
    assert len(ctx_durante_ejecucion) == 1
    ctx = ctx_durante_ejecucion[0]
    assert ctx is not None
    assert ctx.channel_type == "cli"
    assert ctx.user_id == "local"
