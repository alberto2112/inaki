"""Unit tests para ChannelSenderAdapter y HttpCallerAdapter."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from adapters.outbound.scheduler.dispatch_adapters import ChannelSenderAdapter, HttpCallerAdapter
from core.domain.entities.task import WebhookPayload


# ---------------------------------------------------------------------------
# ChannelSenderAdapter
# ---------------------------------------------------------------------------


def _make_channel_sender(bot: MagicMock | None = None) -> tuple[ChannelSenderAdapter, MagicMock]:
    """Crea un ChannelSenderAdapter con un bot mockeado."""
    mock_bot = bot if bot is not None else MagicMock()
    mock_bot.send_message = AsyncMock()
    get_telegram_bot: Callable = MagicMock(return_value=mock_bot)
    adapter = ChannelSenderAdapter(get_telegram_bot=get_telegram_bot)
    return adapter, mock_bot


class TestChannelSenderAdapterTelegram:
    async def test_telegram_prefix_llama_send_message_con_user_id_entero(self) -> None:
        adapter, mock_bot = _make_channel_sender()

        await adapter.send_message("telegram:12345", "Hola!")

        mock_bot.send_message.assert_awaited_once_with(12345, "Hola!")

    async def test_telegram_prefix_convierte_user_id_a_int(self) -> None:
        adapter, mock_bot = _make_channel_sender()

        await adapter.send_message("telegram:99999", "Mensaje de prueba")

        args, _ = mock_bot.send_message.call_args
        assert isinstance(args[0], int)
        assert args[0] == 99999

    async def test_telegram_prefix_invoca_get_telegram_bot_callable(self) -> None:
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()
        mock_get_bot: MagicMock = MagicMock(return_value=mock_bot)
        adapter = ChannelSenderAdapter(get_telegram_bot=mock_get_bot)

        await adapter.send_message("telegram:777", "test")

        mock_get_bot.assert_called_once()

    async def test_telegram_prefix_pasa_texto_correcto(self) -> None:
        adapter, mock_bot = _make_channel_sender()
        texto = "Recordatorio: reunión a las 10am"

        await adapter.send_message("telegram:42", texto)

        mock_bot.send_message.assert_awaited_once_with(42, texto)


class TestChannelSenderAdapterCanalNoSoportado:
    async def test_cli_prefix_lanza_value_error_descriptivo(self) -> None:
        adapter, _ = _make_channel_sender()

        with pytest.raises(ValueError, match="cli"):
            await adapter.send_message("cli:alguno", "texto")

    async def test_rest_prefix_lanza_value_error_descriptivo(self) -> None:
        adapter, _ = _make_channel_sender()

        with pytest.raises(ValueError, match="rest"):
            await adapter.send_message("rest:alguno", "texto")

    async def test_daemon_prefix_lanza_value_error_descriptivo(self) -> None:
        adapter, _ = _make_channel_sender()

        with pytest.raises(ValueError, match="daemon"):
            await adapter.send_message("daemon:alguno", "texto")

    async def test_prefijo_desconocido_lanza_value_error(self) -> None:
        adapter, _ = _make_channel_sender()

        with pytest.raises(ValueError, match="mqtt"):
            await adapter.send_message("mqtt:topic/test", "texto")

    async def test_mensaje_error_cli_menciona_solo_telegram_implementado(self) -> None:
        adapter, _ = _make_channel_sender()

        with pytest.raises(ValueError, match="telegram"):
            await adapter.send_message("cli:alguno", "texto")


class TestChannelSenderAdapterBotNone:
    async def test_telegram_bot_none_lanza_value_error_descriptivo(self) -> None:
        """Si get_telegram_bot devuelve None → ValueError con mensaje claro."""
        get_bot = MagicMock(return_value=None)
        adapter = ChannelSenderAdapter(get_telegram_bot=get_bot)

        with pytest.raises(ValueError, match="no está configurado|no fue registrado"):
            await adapter.send_message("telegram:12345", "Hola")


def _make_payload(**kwargs: object) -> WebhookPayload:
    defaults = {"url": "https://example.com/hook"}
    defaults.update(kwargs)  # type: ignore[arg-type]
    return WebhookPayload(**defaults)  # type: ignore[arg-type]


class TestHttpCallerAdapterSuccess:
    async def test_success_200_returns_response_text(self) -> None:
        adapter = HttpCallerAdapter()
        payload = _make_payload()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "OK"

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("adapters.outbound.scheduler.dispatch_adapters.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = mock_client
            result = await adapter.call(payload)

        assert result == "OK"

    async def test_success_passes_method_url_headers_body_timeout(self) -> None:
        adapter = HttpCallerAdapter()
        payload = _make_payload(
            method="PUT",
            url="https://example.com/resource",
            headers={"X-Token": "abc"},
            body="payload-data",
            timeout=15,
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "Updated"

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("adapters.outbound.scheduler.dispatch_adapters.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = mock_client
            await adapter.call(payload)

        mock_client.request.assert_awaited_once_with(
            method="PUT",
            url="https://example.com/resource",
            headers={"X-Token": "abc"},
            content="payload-data",
            timeout=15,
        )

    async def test_success_204_no_content_in_success_codes(self) -> None:
        adapter = HttpCallerAdapter()
        payload = _make_payload()

        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_response.text = ""

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("adapters.outbound.scheduler.dispatch_adapters.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = mock_client
            result = await adapter.call(payload)

        assert result == ""


class TestHttpCallerAdapterFailure:
    async def test_non_success_status_raises_runtime_error(self) -> None:
        adapter = HttpCallerAdapter()
        payload = _make_payload()

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("adapters.outbound.scheduler.dispatch_adapters.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = mock_client
            with pytest.raises(RuntimeError, match="500"):
                await adapter.call(payload)

    async def test_404_not_in_default_success_codes_raises(self) -> None:
        adapter = HttpCallerAdapter()
        payload = _make_payload()

        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("adapters.outbound.scheduler.dispatch_adapters.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = mock_client
            with pytest.raises(RuntimeError, match="404"):
                await adapter.call(payload)

    async def test_timeout_exception_raises_runtime_error(self) -> None:
        adapter = HttpCallerAdapter()
        payload = _make_payload(timeout=5)

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            side_effect=httpx.TimeoutException("Request timed out")
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("adapters.outbound.scheduler.dispatch_adapters.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = mock_client
            with pytest.raises(RuntimeError, match="timed out"):
                await adapter.call(payload)

    async def test_custom_success_codes_accepted(self) -> None:
        adapter = HttpCallerAdapter()
        payload = _make_payload(success_codes=[201])

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.text = "Created"

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("adapters.outbound.scheduler.dispatch_adapters.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = mock_client
            result = await adapter.call(payload)

        assert result == "Created"

    async def test_connection_refused_raises_runtime_error(self) -> None:
        adapter = HttpCallerAdapter()
        payload = _make_payload()

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("adapters.outbound.scheduler.dispatch_adapters.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = mock_client
            with pytest.raises(RuntimeError, match="connection failed"):
                await adapter.call(payload)

    async def test_custom_success_codes_200_not_included_raises(self) -> None:
        adapter = HttpCallerAdapter()
        payload = _make_payload(success_codes=[201])

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "OK"

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("adapters.outbound.scheduler.dispatch_adapters.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = mock_client
            with pytest.raises(RuntimeError, match="200"):
                await adapter.call(payload)
