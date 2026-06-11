"""Tests de ``dispatch_inbound_turn`` — la política in-flight-message-injection
centralizada que comparten los adapters inbound (Telegram, REST, admin REST).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from adapters.inbound.turn_dispatch import INFLIGHT_ACK, dispatch_inbound_turn

SCOPE = ("dev", "telegram", "123")


@pytest.fixture
def scope_registry() -> MagicMock:
    registry = MagicMock()
    registry.try_mark_busy = AsyncMock(return_value=True)
    registry.mark_idle = AsyncMock(return_value=None)
    return registry


@pytest.fixture
def run_agent() -> MagicMock:
    agent = MagicMock()
    agent.record_user_message = AsyncMock(return_value=None)
    return agent


class TestScopeLibre:
    async def test_ejecuta_el_turno_y_devuelve_la_respuesta(
        self, scope_registry, run_agent
    ) -> None:
        execute = AsyncMock(return_value="respuesta del agente")

        result = await dispatch_inbound_turn(
            scope_registry=scope_registry,
            run_agent=run_agent,
            scope=SCOPE,
            message="hola",
            execute=execute,
        )

        assert result.executed is True
        assert result.reply == "respuesta del agente"
        execute.assert_awaited_once()
        run_agent.record_user_message.assert_not_called()

    async def test_libera_el_slot_despues_de_ejecutar(self, scope_registry, run_agent) -> None:
        await dispatch_inbound_turn(
            scope_registry=scope_registry,
            run_agent=run_agent,
            scope=SCOPE,
            message="hola",
            execute=AsyncMock(return_value="ok"),
        )

        scope_registry.try_mark_busy.assert_awaited_once_with(SCOPE)
        scope_registry.mark_idle.assert_awaited_once_with(SCOPE)

    async def test_libera_el_slot_aunque_execute_lance(self, scope_registry, run_agent) -> None:
        """Garantía clave: un turno que explota NO deja el scope busy para siempre."""
        execute = AsyncMock(side_effect=RuntimeError("boom"))

        with pytest.raises(RuntimeError, match="boom"):
            await dispatch_inbound_turn(
                scope_registry=scope_registry,
                run_agent=run_agent,
                scope=SCOPE,
                message="hola",
                execute=execute,
            )

        scope_registry.mark_idle.assert_awaited_once_with(SCOPE)


class TestScopeOcupado:
    async def test_persiste_el_mensaje_con_el_scope_correcto(
        self, scope_registry, run_agent
    ) -> None:
        """El channel y chat_id del record salen de la tupla scope — el loop
        activo drena history filtrando por ese mismo scope."""
        scope_registry.try_mark_busy = AsyncMock(return_value=False)

        result = await dispatch_inbound_turn(
            scope_registry=scope_registry,
            run_agent=run_agent,
            scope=SCOPE,
            message="dato nuevo",
            execute=AsyncMock(),
        )

        assert result.executed is False
        assert result.reply == INFLIGHT_ACK
        run_agent.record_user_message.assert_awaited_once_with("dato nuevo", "telegram", "123")

    async def test_no_ejecuta_ni_libera_slot_ajeno(self, scope_registry, run_agent) -> None:
        """No adquirimos el slot → no lo liberamos: lo tiene el turno en curso,
        que lo soltará en su propio finally."""
        scope_registry.try_mark_busy = AsyncMock(return_value=False)
        execute = AsyncMock()

        await dispatch_inbound_turn(
            scope_registry=scope_registry,
            run_agent=run_agent,
            scope=SCOPE,
            message="dato nuevo",
            execute=execute,
        )

        execute.assert_not_called()
        scope_registry.mark_idle.assert_not_called()
