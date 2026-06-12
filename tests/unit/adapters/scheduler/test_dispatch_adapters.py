"""Unit tests para ChannelRouter (migra los de ChannelSenderAdapter) y HttpCallerAdapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from adapters.outbound.scheduler.dispatch_adapters import (
    ChannelFallbackSettings,
    ChannelRouter,
    HttpCallerAdapter,
    LLMDispatcherAdapter,
)
from adapters.outbound.sinks.sink_factory import SinkFactory
from adapters.outbound.sinks.telegram_sink import TelegramSink
from core.domain.entities.task import WebhookPayload


# ---------------------------------------------------------------------------
# ChannelRouter — camino Telegram nativo
# ---------------------------------------------------------------------------


def _make_router_with_telegram(
    bot: MagicMock | None = None,
    fallback: ChannelFallbackSettings | None = None,
) -> tuple[ChannelRouter, MagicMock]:
    mock_bot = bot if bot is not None else MagicMock()
    mock_bot.send_message = AsyncMock()
    get_bot = MagicMock(return_value=mock_bot)
    telegram_sink = TelegramSink(get_telegram_bot=get_bot)
    factory = SinkFactory(get_telegram_bot=get_bot)
    router = ChannelRouter(
        native_sinks={"telegram": telegram_sink},
        fallback_config=fallback or ChannelFallbackSettings(),
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
        cfg = ChannelFallbackSettings(default="null:")
        router, _ = _make_router_with_telegram(fallback=cfg)

        result = await router.send_message("cli:alguno", "texto")

        assert result.original_target == "cli:alguno"
        assert result.resolved_target == "null:"

    async def test_rest_prefix_con_override_no_lanza(self) -> None:
        cfg = ChannelFallbackSettings(overrides={"rest": "null:x"})
        router, _ = _make_router_with_telegram(fallback=cfg)

        result = await router.send_message("rest:alguno", "texto")

        assert result.resolved_target == "null:x"

    async def test_daemon_prefix_sin_config_cae_en_hardcoded(self, tmp_path) -> None:
        # Redirigimos el hardcoded a tmp_path para no tocar /tmp real.
        factory = SinkFactory(get_telegram_bot=lambda: None)
        destino = tmp_path / "hc.log"
        router = ChannelRouter(
            native_sinks={},
            fallback_config=ChannelFallbackSettings(),
            sink_factory=factory.from_target,
            hardcoded_fallback=f"file://{destino}",
        )

        result = await router.send_message("daemon:alguno", "texto")

        assert result.original_target == "daemon:alguno"
        assert result.resolved_target == f"file://{destino}"
        assert destino.exists()

    async def test_prefijo_desconocido_cae_en_fallback_no_lanza(self) -> None:
        cfg = ChannelFallbackSettings(default="null:")
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
            fallback_config=ChannelFallbackSettings(),
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
        mock_client.request = AsyncMock(side_effect=httpx.TimeoutException("Request timed out"))
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
        mock_client.request = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
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


# ---------------------------------------------------------------------------
# LLMDispatcherAdapter — lock-per-scope (REQ-BGD-6)
# ---------------------------------------------------------------------------


def _build_recording_agent(events: list[str], *, sleep_s: float = 0.01) -> MagicMock:
    """Construye un agente mock cuya `run_agent.execute` registra
    ``start:<prompt>`` y ``end:<prompt>`` en ``events`` con un ``sleep`` en el
    medio. Si dos invocaciones corren en paralelo sin lock, los eventos se
    intercalan; con lock, quedan agrupados por invocación.
    """
    import asyncio

    async def fake_execute(prompt: str, **_kw) -> str:
        events.append(f"start:{prompt}")
        await asyncio.sleep(sleep_s)
        events.append(f"end:{prompt}")
        return prompt

    agent = MagicMock()
    agent.run_agent.execute = AsyncMock(side_effect=fake_execute)
    return agent


class TestLLMDispatcherAdapterLockPerScope:
    """Dos dispatches concurrentes sobre el mismo ``(agent_id, channel, chat_id)``
    deben serializarse — el adapter adquiere un ``asyncio.Lock`` lazy-init por
    scope. Dispatches a scopes distintos NO comparten lock.
    """

    async def test_mismo_scope_se_serializa(self) -> None:
        import asyncio

        events: list[str] = []
        agent = _build_recording_agent(events)
        dispatcher = LLMDispatcherAdapter({"inaki": agent})

        await asyncio.gather(
            dispatcher.dispatch("inaki", "msg1", channel="telegram", chat_id="42"),
            dispatcher.dispatch("inaki", "msg2", channel="telegram", chat_id="42"),
        )

        # Con lock: cada invocación completa antes de que arranque la otra.
        # events[0]/events[1] corresponden al mismo prompt; events[2]/events[3]
        # al otro. Sin lock, habría interleaving (start:msg1, start:msg2, ...).
        assert len(events) == 4
        primer_prompt = events[0].split(":", 1)[1]
        segundo_prompt = events[2].split(":", 1)[1]
        assert events[0] == f"start:{primer_prompt}"
        assert events[1] == f"end:{primer_prompt}"
        assert events[2] == f"start:{segundo_prompt}"
        assert events[3] == f"end:{segundo_prompt}"
        assert primer_prompt != segundo_prompt

    async def test_scopes_distintos_usan_locks_distintos(self) -> None:
        """Triangulación: el lock es POR scope, no global. Dos dispatches a
        scopes distintos no contienden — el dict ``_locks`` del adapter debe
        contener dos entradas tras el ejercicio.
        """
        import asyncio

        events: list[str] = []
        agent = _build_recording_agent(events, sleep_s=0.005)
        dispatcher = LLMDispatcherAdapter({"inaki": agent})

        await asyncio.gather(
            dispatcher.dispatch("inaki", "msg1", channel="telegram", chat_id="42"),
            dispatcher.dispatch("inaki", "msg2", channel="telegram", chat_id="99"),
        )

        # El adapter expone su dict de locks para inspección/test
        locks = getattr(dispatcher, "_locks", None)
        assert locks is not None, "Adapter debe exponer _locks como dict interno"
        assert ("inaki", "telegram", "42") in locks
        assert ("inaki", "telegram", "99") in locks
        assert len(locks) == 2

    async def test_lock_se_libera_aunque_execute_lance(self) -> None:
        """El lock debe liberarse si ``run_agent.execute`` lanza una excepción;
        de lo contrario el siguiente dispatch al mismo scope quedaría colgado.
        """
        import asyncio

        agent = MagicMock()
        agent.run_agent.execute = AsyncMock(side_effect=RuntimeError("boom"))
        dispatcher = LLMDispatcherAdapter({"inaki": agent})

        with pytest.raises(RuntimeError, match="boom"):
            await dispatcher.dispatch("inaki", "x", channel="cli", chat_id="")

        # Segunda llamada al mismo scope no debe colgarse — si el lock quedó
        # tomado, este await timeoutearía.
        agent.run_agent.execute = AsyncMock(return_value="ok")
        result = await asyncio.wait_for(
            dispatcher.dispatch("inaki", "y", channel="cli", chat_id=""),
            timeout=1.0,
        )
        assert result == "ok"


# ---------------------------------------------------------------------------
# ShellExecAdapter
# ---------------------------------------------------------------------------


async def test_shell_exec_devuelve_stdout() -> None:
    from adapters.outbound.scheduler.dispatch_adapters import ShellExecAdapter
    from core.domain.entities.task import ShellExecPayload

    adapter = ShellExecAdapter()
    out = await adapter.run(ShellExecPayload(command="echo hola"))
    assert out.strip() == "hola"


async def test_shell_exec_exit_code_no_cero_lanza() -> None:
    from adapters.outbound.scheduler.dispatch_adapters import ShellExecAdapter
    from core.domain.entities.task import ShellExecPayload

    adapter = ShellExecAdapter()
    with pytest.raises(RuntimeError, match="exited with code"):
        await adapter.run(ShellExecPayload(command="exit 3"))


async def test_shell_exec_timeout_mata_el_proceso() -> None:
    """Al expirar el timeout, el subprocess debe ser terminado — no quedar
    corriendo huérfano mientras el retry lanza otro encima."""
    import time

    from adapters.outbound.scheduler.dispatch_adapters import ShellExecAdapter
    from core.domain.entities.task import ShellExecPayload

    adapter = ShellExecAdapter()
    start = time.monotonic()
    with pytest.raises(RuntimeError, match="timeout"):
        await adapter.run(ShellExecPayload(command="sleep 30", timeout=1))
    # Si el kill funcionó, volvemos apenas pasado el timeout (no los 30s del sleep)
    assert time.monotonic() - start < 5
