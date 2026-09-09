"""``LLMDispatcherAdapter``: un lock por scope ``(agent_id, channel, chat_id)``."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from inaki.agents.dispatcher import LLMDispatcherAdapter


class TestLLMDispatcherAdapterLockPerScope:
    """Dos dispatches concurrentes sobre el mismo ``(agent_id, channel, chat_id)``
    deben serializarse — el adapter adquiere un ``asyncio.Lock`` lazy-init por
    scope. Dispatches a scopes distintos NO comparten lock.
    """

    async def test_mismo_scope_se_serializa(self) -> None:

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
    from inaki.scheduler.adapters.dispatch import ShellExecAdapter
    from inaki.scheduler.domain.task import ShellExecPayload

    adapter = ShellExecAdapter()
    out = await adapter.run(ShellExecPayload(command="echo hola"))
    assert out.strip() == "hola"


async def test_shell_exec_exit_code_no_cero_lanza() -> None:
    from inaki.scheduler.adapters.dispatch import ShellExecAdapter
    from inaki.scheduler.domain.task import ShellExecPayload

    adapter = ShellExecAdapter()
    with pytest.raises(RuntimeError, match="exited with code"):
        await adapter.run(ShellExecPayload(command="exit 3"))


async def test_shell_exec_timeout_mata_el_proceso() -> None:
    """Al expirar el timeout, el subprocess debe ser terminado — no quedar
    corriendo huérfano mientras el retry lanza otro encima."""
    import time

    from inaki.scheduler.adapters.dispatch import ShellExecAdapter
    from inaki.scheduler.domain.task import ShellExecPayload

    adapter = ShellExecAdapter()
    start = time.monotonic()
    with pytest.raises(RuntimeError, match="timeout"):
        await adapter.run(ShellExecPayload(command="sleep 30", timeout=1))
    # Si el kill funcionó, volvemos apenas pasado el timeout (no los 30s del sleep)
    assert time.monotonic() - start < 5


def _build_recording_agent(events: list[str], *, sleep_s: float = 0.01) -> MagicMock:
    """Construye un agente mock cuya `run_agent.execute` registra
    ``start:<prompt>`` y ``end:<prompt>`` en ``events`` con un ``sleep`` en el
    medio. Si dos invocaciones corren en paralelo sin lock, los eventos se
    intercalan; con lock, quedan agrupados por invocación.
    """

    async def fake_execute(prompt: str, **_kw) -> str:
        events.append(f"start:{prompt}")
        await asyncio.sleep(sleep_s)
        events.append(f"end:{prompt}")
        return prompt

    agent = MagicMock()
    agent.run_agent.execute = AsyncMock(side_effect=fake_execute)
    return agent
