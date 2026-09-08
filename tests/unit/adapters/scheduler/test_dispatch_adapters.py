"""Unit tests de los dispatch adapters del scheduler: HttpCallerAdapter y LLMDispatcherAdapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from adapters.outbound.scheduler.dispatch_adapters import (
    HttpCallerAdapter,
    LLMDispatcherAdapter,
)
from core.domain.entities.task import WebhookPayload


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


def _make_agent_with_history() -> MagicMock:
    agent = MagicMock()
    agent.history.append = AsyncMock(return_value=1)
    return agent


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
