"""Tests de la drainage de history.db en el tool loop (in-flight-message-injection).

Verifica el comportamiento del feature ``in-flight-message-injection``:
- Los mensajes ``role=user`` que aparecen en ``history.db`` mientras el tool loop
  está corriendo se drenan en los checkpoints A (antes del llm.complete) y B
  (después del batch de tool_calls).
- El contador de iteraciones se resetea cuando hay drain no-vacío.
- Cuando no se pasa ``history_store`` y/o ``scope``, el loop corre en modo legacy.
"""

from __future__ import annotations

import json

from unittest.mock import AsyncMock

from core.domain.entities.message import Message, Role
from core.domain.errors import ToolLoopMaxIterationsError
from core.domain.value_objects.conversation_state import ConversationState
from core.domain.value_objects.llm_response import LLMResponse
from core.ports.outbound.history_port import IHistoryStore
from core.ports.outbound.scope_registry_port import Scope
from core.ports.outbound.tool_port import ToolResult
from core.use_cases._tool_loop import run_tool_loop


# ---------------------------------------------------------------------------
# Fake de IHistoryStore — solo load() es significativo; el resto son no-ops.
# ---------------------------------------------------------------------------


class _FakeHistoryStore(IHistoryStore):
    """Stub mínimo para tests del drainage.

    El test muta directamente ``self.messages`` para simular que un inbound
    adapter persistió un mensaje nuevo mientras el tool loop corre.
    """

    def __init__(self, initial: list[Message] | None = None) -> None:
        self.messages: list[Message] = list(initial or [])

    async def load(
        self,
        agent_id: str,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> list[Message]:
        return list(self.messages)

    # Métodos no usados por el tool loop — stubs para satisfacer el ABC.
    async def append(self, *args, **kwargs) -> int | None:  # noqa: ARG002
        return None

    async def update_content(self, *args, **kwargs) -> bool:  # noqa: ARG002
        return False

    async def load_full(self, *args, **kwargs) -> list[Message]:  # noqa: ARG002
        return list(self.messages)

    async def load_uninfused(self, *args, **kwargs) -> list[Message]:  # noqa: ARG002
        return []

    async def mark_infused(self, *args, **kwargs) -> int:  # noqa: ARG002
        return 0

    async def trim(self, *args, **kwargs) -> None:  # noqa: ARG002
        return None

    async def clear(self, *args, **kwargs) -> None:  # noqa: ARG002
        return None

    async def load_state(self, *args, **kwargs) -> ConversationState:  # noqa: ARG002
        return ConversationState(sticky_skills={}, sticky_tools={})

    async def save_state(self, *args, **kwargs) -> None:  # noqa: ARG002
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tool_call_response(tool_name: str = "search") -> LLMResponse:
    return LLMResponse(
        text_blocks=[],
        tool_calls=[
            {
                "function": {
                    "name": tool_name,
                    "arguments": json.dumps({}),
                }
            }
        ],
        raw="",
    )


def _make_tools(success: bool = True) -> AsyncMock:
    tools = AsyncMock()
    tools.execute = AsyncMock(
        return_value=ToolResult(tool_name="search", output="resultado", success=success)
    )
    return tools


_SCOPE: Scope = ("agent1", "telegram", "chat1")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_drained_message_is_visible_to_llm_in_next_iteration():
    """Escenario Paris: M2 aparece en history durante iter 1; iter 2 lo ve."""
    initial_msg = Message(role=Role.USER, content="estudio mercado peluquería")
    history = _FakeHistoryStore(initial=[initial_msg])

    call_count = 0
    seen_paris_in_iter_2 = False

    async def llm_complete(messages, system_prompt, tools=None):  # noqa: ARG001
        nonlocal call_count, seen_paris_in_iter_2
        call_count += 1
        if call_count == 1:
            # Durante iter 1, simulamos que el inbound adapter persistió un
            # mensaje nuevo en history (como haría el branch busy).
            history.messages.append(Message(role=Role.USER, content="incluí Paris"))
            return _tool_call_response()
        # iter 2: checkpoint A drenó "incluí Paris" antes de esta llamada.
        seen_paris_in_iter_2 = any(m.content == "incluí Paris" for m in messages)
        return LLMResponse.of_text("Listo, estudio incluye Paris")

    llm = AsyncMock()
    llm.complete = AsyncMock(side_effect=llm_complete)
    llm.thinking_active = False

    result = await run_tool_loop(
        llm=llm,
        tools=_make_tools(),
        messages=[initial_msg],
        system_prompt="x",
        tool_schemas=[],
        max_iterations=5,
        circuit_breaker_threshold=3,
        agent_id="agent1",
        history_store=history,
        scope=_SCOPE,
    )

    assert result == "Listo, estudio incluye Paris"
    assert seen_paris_in_iter_2, "iter 2 debía ver 'incluí Paris' en working_messages"


async def test_counter_resets_when_drain_returns_messages():
    """Con max_iterations=2 y un push en iter 2, deben caber al menos 2 iteraciones más."""
    initial_msg = Message(role=Role.USER, content="primera tarea")
    history = _FakeHistoryStore(initial=[initial_msg])

    call_count = 0

    async def llm_complete(messages, system_prompt, tools=None):  # noqa: ARG001
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _tool_call_response()
        if call_count == 2:
            # Después del batch de iter 2, simulamos el push del usuario.
            # checkpoint B lo drena → iteration vuelve a 0.
            history.messages.append(Message(role=Role.USER, content="ahora hacé otra cosa"))
            return _tool_call_response()
        # Sin reset, llamar 3+ veces sería imposible con max_iterations=2.
        # Si llegamos acá, el reset funcionó.
        return LLMResponse.of_text("ok hecho")

    llm = AsyncMock()
    llm.complete = AsyncMock(side_effect=llm_complete)
    llm.thinking_active = False

    result = await run_tool_loop(
        llm=llm,
        tools=_make_tools(),
        messages=[initial_msg],
        system_prompt="x",
        tool_schemas=[],
        max_iterations=2,
        circuit_breaker_threshold=3,
        agent_id="agent1",
        history_store=history,
        scope=_SCOPE,
    )

    assert result == "ok hecho"
    # 3 llamadas demuestra que con max=2 el reset permitió la tercera.
    assert call_count == 3


async def test_empty_drain_does_not_reset_counter():
    """Sin pushes nuevos, el loop respeta max_iterations exactamente como antes."""
    initial_msg = Message(role=Role.USER, content="hola")
    history = _FakeHistoryStore(initial=[initial_msg])

    call_count = 0

    async def llm_complete(messages, system_prompt, tools=None):  # noqa: ARG001
        nonlocal call_count
        call_count += 1
        return (
            _tool_call_response()
        )  # SIEMPRE devuelve tool_calls → loop nunca termina por respuesta final

    llm = AsyncMock()
    llm.complete = AsyncMock(side_effect=llm_complete)
    llm.thinking_active = False

    try:
        await run_tool_loop(
            llm=llm,
            tools=_make_tools(),
            messages=[initial_msg],
            system_prompt="x",
            tool_schemas=[],
            max_iterations=3,
            circuit_breaker_threshold=10,
            agent_id="agent1",
            history_store=history,
            scope=_SCOPE,
        )
        assert False, "Debería haber alcanzado max_iterations"
    except ToolLoopMaxIterationsError:
        pass

    # 3 iteraciones LLM + 1 fallback call sin tools (recuperación de last_text vacío).
    assert call_count == 4


async def test_backward_compat_no_history_store():
    """Sin history_store, el loop corre como antes (sin drainage)."""
    initial_msg = Message(role=Role.USER, content="hola")

    llm = AsyncMock()
    llm.complete = AsyncMock(return_value=LLMResponse.of_text("respuesta"))
    llm.thinking_active = False

    result = await run_tool_loop(
        llm=llm,
        tools=_make_tools(),
        messages=[initial_msg],
        system_prompt="x",
        tool_schemas=[],
        max_iterations=5,
        circuit_breaker_threshold=3,
        agent_id="agent1",
        # SIN history_store ni scope — modo legacy.
    )

    assert result == "respuesta"
    assert llm.complete.await_count == 1


async def test_backward_compat_scope_without_history():
    """Pasar scope sin history_store NO debe activar drainage (defensive)."""
    initial_msg = Message(role=Role.USER, content="hola")

    llm = AsyncMock()
    llm.complete = AsyncMock(return_value=LLMResponse.of_text("respuesta"))
    llm.thinking_active = False

    result = await run_tool_loop(
        llm=llm,
        tools=_make_tools(),
        messages=[initial_msg],
        system_prompt="x",
        tool_schemas=[],
        max_iterations=5,
        circuit_breaker_threshold=3,
        agent_id="agent1",
        history_store=None,
        scope=_SCOPE,
    )

    assert result == "respuesta"


async def test_drained_messages_are_not_re_drained():
    """Un mensaje drenado en checkpoint A NO se re-drena en checkpoint B de la misma iteración."""
    initial_msg = Message(role=Role.USER, content="hola")
    history = _FakeHistoryStore(initial=[initial_msg])
    # Simulamos que ya había un mensaje pending ANTES de iniciar el loop
    # (debería drenarse en checkpoint A de iter 1, pero el initial_user_count
    # ya lo cuenta — así que NO se drena).
    history.messages.append(Message(role=Role.USER, content="ya estaba"))

    call_count = 0
    seen_messages_in_iter_2: list[Message] = []

    async def llm_complete(messages, system_prompt, tools=None):  # noqa: ARG001
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _tool_call_response()
        seen_messages_in_iter_2.extend(messages)
        return LLMResponse.of_text("fin")

    llm = AsyncMock()
    llm.complete = AsyncMock(side_effect=llm_complete)
    llm.thinking_active = False

    # IMPORTANTE: messages contiene los DOS user-msgs porque ambos estaban en
    # history al iniciar el loop. initial_user_count = 2. Cualquier otro
    # role=user en history posterior se drenaría — pero acá no hay.
    result = await run_tool_loop(
        llm=llm,
        tools=_make_tools(),
        messages=[initial_msg, history.messages[1]],
        system_prompt="x",
        tool_schemas=[],
        max_iterations=5,
        circuit_breaker_threshold=3,
        agent_id="agent1",
        history_store=history,
        scope=_SCOPE,
    )

    assert result == "fin"
    # Solo deberían estar los 2 mensajes iniciales como role=user — sin duplicados.
    user_contents = [m.content for m in seen_messages_in_iter_2 if m.role == Role.USER]
    assert user_contents.count("hola") == 1
    assert user_contents.count("ya estaba") == 1


async def test_initial_db_user_count_respeta_coalesce():
    """Cuando ``messages`` viene coalesced, ``initial_db_user_count`` evita el drain
    falso de mensajes que ya están dentro del bloque coalesced.

    Escenario history-derived (modo flush de grupo):
    - DB tiene 3 user-msgs consecutivos [u1, u2, u3].
    - `_coalesce_consecutive_same_role` los une en `[user("u1\\nu2\\nu3")]`.
    - SIN initial_db_user_count: el loop cuenta 1 user_msg en messages,
      pero la DB tiene 3 → drain reinyecta [u2, u3] como duplicados visibles
      al LLM ("historial clonado").
    - CON initial_db_user_count=3: drain compara 3 vs 3 → no drena nada.
    """
    u1 = Message(role=Role.USER, content="u1")
    u2 = Message(role=Role.USER, content="u2")
    u3 = Message(role=Role.USER, content="u3")
    history = _FakeHistoryStore(initial=[u1, u2, u3])
    coalesced = Message(role=Role.USER, content="u1\nu2\nu3")

    seen_user_msgs: list[str] = []

    async def llm_complete(messages, system_prompt, tools=None):  # noqa: ARG001
        seen_user_msgs.extend(m.content for m in messages if m.role == Role.USER)
        return LLMResponse.of_text("ok")

    llm = AsyncMock()
    llm.complete = AsyncMock(side_effect=llm_complete)
    llm.thinking_active = False

    await run_tool_loop(
        llm=llm,
        tools=_make_tools(),
        messages=[coalesced],  # lo que ve el LLM tras coalesce
        system_prompt="x",
        tool_schemas=[],
        max_iterations=5,
        circuit_breaker_threshold=3,
        agent_id="agent1",
        history_store=history,
        scope=_SCOPE,
        initial_db_user_count=3,  # el caller (execute()) cuenta sobre history crudo
    )

    # El LLM solo debería ver el bloque coalesced — sin re-inyecciones de u2/u3.
    assert seen_user_msgs == ["u1\nu2\nu3"]


async def test_initial_db_user_count_permite_drenar_nuevo_msg_post_coalesce():
    """Con coalesce + baseline correcto, los mensajes GENUINAMENTE nuevos siguen drenándose."""
    u1 = Message(role=Role.USER, content="u1")
    u2 = Message(role=Role.USER, content="u2")
    history = _FakeHistoryStore(initial=[u1, u2])
    coalesced = Message(role=Role.USER, content="u1\nu2")

    call_count = 0
    seen_messages_iter_2: list[str] = []

    async def llm_complete(messages, system_prompt, tools=None):  # noqa: ARG001
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Durante iter 1, llega un mensaje genuinamente nuevo.
            history.messages.append(Message(role=Role.USER, content="cancela todo"))
            return _tool_call_response()
        # iter 2: checkpoint A drenó "cancela todo".
        seen_messages_iter_2.extend(m.content for m in messages if m.role == Role.USER)
        return LLMResponse.of_text("entendido, cancelado")

    llm = AsyncMock()
    llm.complete = AsyncMock(side_effect=llm_complete)
    llm.thinking_active = False

    result = await run_tool_loop(
        llm=llm,
        tools=_make_tools(),
        messages=[coalesced],
        system_prompt="x",
        tool_schemas=[],
        max_iterations=5,
        circuit_breaker_threshold=3,
        agent_id="agent1",
        history_store=history,
        scope=_SCOPE,
        initial_db_user_count=2,
    )

    assert result == "entendido, cancelado"
    # iter 2 ve el bloque coalesced ORIGINAL + el nuevo drenado, sin duplicar u1/u2.
    assert seen_messages_iter_2 == ["u1\nu2", "cancela todo"]


async def test_drain_at_checkpoint_b_after_tools():
    """Push DURANTE la ejecución de tools (entre LLM call y checkpoint B) se drena en B."""
    initial_msg = Message(role=Role.USER, content="hola")
    history = _FakeHistoryStore(initial=[initial_msg])

    call_count = 0
    seen_paris_in_iter_2 = False

    # Tools mock con side_effect que simula el push DURANTE la ejecución de la tool.
    tools = AsyncMock()

    async def tools_exec(tool_name, **kwargs):  # noqa: ARG001
        # Simula que mientras la tool corre (segundos), el inbound persistió un msg.
        history.messages.append(Message(role=Role.USER, content="incluí Paris"))
        return ToolResult(tool_name=tool_name, output="ok", success=True)

    tools.execute = AsyncMock(side_effect=tools_exec)

    async def llm_complete(messages, system_prompt, tools=None):  # noqa: ARG001
        nonlocal call_count, seen_paris_in_iter_2
        call_count += 1
        if call_count == 1:
            return _tool_call_response()
        seen_paris_in_iter_2 = any(m.content == "incluí Paris" for m in messages)
        return LLMResponse.of_text("hecho")

    llm = AsyncMock()
    llm.complete = AsyncMock(side_effect=llm_complete)
    llm.thinking_active = False

    result = await run_tool_loop(
        llm=llm,
        tools=tools,
        messages=[initial_msg],
        system_prompt="x",
        tool_schemas=[],
        max_iterations=5,
        circuit_breaker_threshold=3,
        agent_id="agent1",
        history_store=history,
        scope=_SCOPE,
    )

    assert result == "hecho"
    # Importante: el mensaje "incluí Paris" llegó DURANTE la ejecución de la tool
    # de iter 1 — checkpoint B (después del batch) o checkpoint A de iter 2 lo verá.
    assert seen_paris_in_iter_2
