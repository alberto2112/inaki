"""Unit tests para ChannelRouter (migra los de ChannelSenderAdapter) y HttpCallerAdapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from adapters.outbound.scheduler.dispatch_adapters import ChannelRouter, HttpCallerAdapter
from adapters.outbound.sinks.sink_factory import SinkFactory
from adapters.outbound.sinks.telegram_sink import TelegramSink
from core.domain.entities.task import WebhookPayload
from infrastructure.config import ChannelFallbackConfig


# ---------------------------------------------------------------------------
# ChannelRouter — camino Telegram nativo
# ---------------------------------------------------------------------------


def _make_router_with_telegram(
    bot: MagicMock | None = None,
    fallback: ChannelFallbackConfig | None = None,
) -> tuple[ChannelRouter, MagicMock]:
    mock_bot = bot if bot is not None else MagicMock()
    mock_bot.send_message = AsyncMock()
    get_bot = MagicMock(return_value=mock_bot)
    telegram_sink = TelegramSink(get_telegram_bot=get_bot)
    factory = SinkFactory(get_telegram_bot=get_bot)
    router = ChannelRouter(
        native_sinks={"telegram": telegram_sink},
        fallback_config=fallback or ChannelFallbackConfig(),
        sink_factory=factory.from_target,
    )
    return router, mock_bot


class TestChannelRouterTelegramNativo:
    async def test_telegram_prefix_llama_send_message_con_user_id_entero(self) -> None:
        router, mock_bot = _make_router_with_telegram()

        await router.send_message("telegram:12345", "Hola!")

        mock_bot.send_message.assert_awaited_once_with(12345, "Hola!")

    async def test_telegram_prefix_convierte_user_id_a_int(self) -> None:
        router, mock_bot = _make_router_with_telegram()

        await router.send_message("telegram:99999", "Mensaje de prueba")

        args, _ = mock_bot.send_message.call_args
        assert isinstance(args[0], int)
        assert args[0] == 99999

    async def test_telegram_prefix_pasa_texto_correcto(self) -> None:
        router, mock_bot = _make_router_with_telegram()
        texto = "Recordatorio: reunión a las 10am"

        await router.send_message("telegram:42", texto)

        mock_bot.send_message.assert_awaited_once_with(42, texto)


class TestChannelRouterCanalesInboundConFallback:
    """Antes los canales inbound (cli/rest/daemon) lanzaban ValueError.

    Con ChannelRouter + fallback ya NUNCA lanzan por canal: siempre cae
    en override → default → hardcoded. Mantenemos la intención del test
    original (evitar errores silenciosos) pero verificando el nuevo contrato.
    """

    async def test_cli_prefix_con_default_null_no_lanza(self) -> None:
        cfg = ChannelFallbackConfig(default="null:")
        router, _ = _make_router_with_telegram(fallback=cfg)

        result = await router.send_message("cli:alguno", "texto")

        assert result.original_target == "cli:alguno"
        assert result.resolved_target == "null:"

    async def test_rest_prefix_con_override_no_lanza(self) -> None:
        cfg = ChannelFallbackConfig(overrides={"rest": "null:x"})
        router, _ = _make_router_with_telegram(fallback=cfg)

        result = await router.send_message("rest:alguno", "texto")

        assert result.resolved_target == "null:x"

    async def test_daemon_prefix_sin_config_cae_en_hardcoded(self, tmp_path) -> None:
        # Redirigimos el hardcoded a tmp_path para no tocar /tmp real.
        factory = SinkFactory(get_telegram_bot=lambda: None)
        destino = tmp_path / "hc.log"
        router = ChannelRouter(
            native_sinks={},
            fallback_config=ChannelFallbackConfig(),
            sink_factory=factory.from_target,
            hardcoded_fallback=f"file://{destino}",
        )

        result = await router.send_message("daemon:alguno", "texto")

        assert result.original_target == "daemon:alguno"
        assert result.resolved_target == f"file://{destino}"
        assert destino.exists()

    async def test_prefijo_desconocido_cae_en_fallback_no_lanza(self) -> None:
        cfg = ChannelFallbackConfig(default="null:")
        router, _ = _make_router_with_telegram(fallback=cfg)

        result = await router.send_message("mqtt:topic/test", "texto")

        assert result.resolved_target == "null:"


class TestChannelRouterTelegramBotNone:
    async def test_telegram_bot_none_lanza_value_error_descriptivo(self) -> None:
        """Si el sink nativo de Telegram aplica y el bot no está registrado,
        TelegramSink levanta ValueError — el router no oculta ese error."""
        get_bot = MagicMock(return_value=None)
        telegram_sink = TelegramSink(get_telegram_bot=get_bot)
        factory = SinkFactory(get_telegram_bot=get_bot)
        router = ChannelRouter(
            native_sinks={"telegram": telegram_sink},
            fallback_config=ChannelFallbackConfig(),
            sink_factory=factory.from_target,
        )

        with pytest.raises(ValueError, match="no está configurado|no fue registrado"):
            await router.send_message("telegram:12345", "Hola")


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
