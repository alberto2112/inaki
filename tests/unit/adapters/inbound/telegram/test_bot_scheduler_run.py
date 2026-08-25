"""Tests para el sub-comando `/scheduler run <id>` del TelegramBot.

Cubre el disparo manual on-demand (espejo de `inaki scheduler run <id>`):
  - Camino feliz: llama a IManualTaskRunner.run_task_now y reporta el resultado.
  - Output largo → se trocea (Telegram rechaza > 4096 chars con BadRequest).
  - Trigger fallido (success=False) → mensaje de fallo, no de error de comando.
  - Tarea inexistente → "no encontrada" (TaskNotFoundError la mapea el caller).
  - Sin id / id no numérico → mensajes de uso.
  - runner None (scheduler no wireado) → aviso de no disponible.
  - Usuario no autorizado → no hace nada.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from adapters.inbound.telegram.ports import TelegramChannelSettings
from core.domain.errors import TaskNotFoundError
from core.domain.value_objects.manual_run_result import ManualRunResult


@pytest.fixture
def mock_runner() -> MagicMock:
    """Mock de IManualTaskRunner con una corrida exitosa por default."""
    runner = MagicMock()
    runner.run_task_now = AsyncMock(
        return_value=ManualRunResult(task_id=107, success=True, output="listo el informe")
    )
    return runner


@pytest.fixture
def mock_ports(mock_runner) -> MagicMock:
    """TelegramBotPorts con scheduler wireado (schedule_task + manual_task_runner)."""
    ports = MagicMock()
    ports.schedule_task = MagicMock()
    ports.manual_task_runner = mock_runner
    return ports


@pytest.fixture
def settings() -> MagicMock:
    cfg = MagicMock()
    cfg.id = "dev"
    cfg.name = "Inaki"
    cfg.description = "Asistente"
    cfg.telegram = TelegramChannelSettings(
        token="dummy-token", allowed_user_ids=("12345",), reactions=False
    )
    return cfg


def _build_bot(settings, ports):
    with patch("adapters.inbound.telegram.bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        builder = mock_app_cls.builder.return_value.token.return_value
        builder.concurrent_updates.return_value.connect_timeout.return_value.read_timeout.return_value.write_timeout.return_value.pool_timeout.return_value.build.return_value = mock_app
        from adapters.inbound.telegram.bot import TelegramBot

        return TelegramBot(settings=settings, ports=ports)


@pytest.fixture
def bot(settings, mock_ports):
    return _build_bot(settings, mock_ports)


def _update_and_context(args: list[str], user_id: int = 12345):
    update = MagicMock()
    update.effective_user.id = user_id
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.args = args
    return update, context


async def test_run_dispara_la_tarea_y_reporta_output(bot, mock_runner) -> None:
    """`/scheduler run 107` llama run_task_now(107) y muestra el output."""
    update, context = _update_and_context(["run", "107"])

    await bot._cmd_scheduler(update, context)

    mock_runner.run_task_now.assert_awaited_once_with(107)
    textos = [c.args[0] for c in update.message.reply_text.call_args_list]
    assert any("Disparando tarea 107" in t for t in textos)
    assert any("Tarea 107 ejecutada — agenda intacta." in t for t in textos)
    assert any("listo el informe" in t for t in textos)


async def test_run_sin_output_no_manda_mensaje_vacio(bot, mock_runner) -> None:
    """Un trigger sin salida (channel_send) solo confirma la ejecución."""
    mock_runner.run_task_now.return_value = ManualRunResult(task_id=42, success=True, output=None)
    update, context = _update_and_context(["run", "42"])

    await bot._cmd_scheduler(update, context)

    textos = [c.args[0] for c in update.message.reply_text.call_args_list]
    assert textos == ["Disparando tarea 42...", "Tarea 42 ejecutada — agenda intacta."]


async def test_run_output_largo_se_trocea(bot, mock_runner) -> None:
    """Un output de agent_send > 4096 chars se parte: reply_text crudo daría BadRequest."""
    largo = "\n".join(f"linea {i} con relleno suficiente para sumar" * 3 for i in range(300))
    mock_runner.run_task_now.return_value = ManualRunResult(task_id=7, success=True, output=largo)
    update, context = _update_and_context(["run", "7"])

    await bot._cmd_scheduler(update, context)

    # Los dos primeros mensajes son el ACK y la confirmación; el resto, el output.
    chunks = [c.args[0] for c in update.message.reply_text.call_args_list][2:]
    assert len(chunks) > 1
    assert all(len(c) <= 4096 for c in chunks)


async def test_run_trigger_fallido_reporta_el_error_del_trigger(bot, mock_runner) -> None:
    """success=False NO es un error del comando: se reporta el fallo del trigger."""
    mock_runner.run_task_now.return_value = ManualRunResult(
        task_id=107, success=False, error="timeout del provider"
    )
    update, context = _update_and_context(["run", "107"])

    await bot._cmd_scheduler(update, context)

    textos = [c.args[0] for c in update.message.reply_text.call_args_list]
    assert any("Tarea 107 falló: timeout del provider" in t for t in textos)
    assert not any("ejecutada" in t for t in textos)


async def test_run_tarea_inexistente(bot, mock_runner) -> None:
    """TaskNotFoundError la mapea el handler compartido de _cmd_scheduler."""
    mock_runner.run_task_now.side_effect = TaskNotFoundError("Task 999 not found")
    update, context = _update_and_context(["run", "999"])

    await bot._cmd_scheduler(update, context)

    textos = [c.args[0] for c in update.message.reply_text.call_args_list]
    assert any("Tarea 999 no encontrada." in t for t in textos)


async def test_run_sin_id_muestra_uso(bot, mock_runner) -> None:
    update, context = _update_and_context(["run"])

    await bot._cmd_scheduler(update, context)

    mock_runner.run_task_now.assert_not_awaited()
    update.message.reply_text.assert_awaited_once_with("Uso: /scheduler run <id>")


async def test_run_id_no_numerico(bot, mock_runner) -> None:
    update, context = _update_and_context(["run", "abc"])

    await bot._cmd_scheduler(update, context)

    mock_runner.run_task_now.assert_not_awaited()
    update.message.reply_text.assert_awaited_once_with("ID inválido: abc")


async def test_run_sin_runner_avisa_no_disponible(settings, mock_ports) -> None:
    """Scheduler no wireado en este proceso → aviso explícito, sin explotar."""
    mock_ports.manual_task_runner = None
    bot = _build_bot(settings, mock_ports)
    update, context = _update_and_context(["run", "107"])

    await bot._cmd_scheduler(update, context)

    textos = [c.args[0] for c in update.message.reply_text.call_args_list]
    assert any("El disparo manual no está disponible en este proceso." in t for t in textos)


async def test_run_usuario_no_autorizado_no_dispara(bot, mock_runner) -> None:
    update, context = _update_and_context(["run", "107"], user_id=99999)

    await bot._cmd_scheduler(update, context)

    mock_runner.run_task_now.assert_not_awaited()
    update.message.reply_text.assert_not_called()


async def test_subcomando_desconocido_menciona_run(bot) -> None:
    """El mensaje de ayuda del fallback lista `run` entre las opciones."""
    update, context = _update_and_context(["frobnicate"])

    await bot._cmd_scheduler(update, context)

    texto = update.message.reply_text.call_args.args[0]
    assert "run" in texto
