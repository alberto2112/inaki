"""Integration E2E del chat CLI vía REST (issue #8 / verify-report S1 de ``cli-chat-via-rest``).

Los 81 tests unitarios cubren handlers, schemas, ``DaemonClient`` y el CLI runner
por separado. Ninguno ejercita el flujo COMPLETO de extremo a extremo:

    run_cli (REPL real)
      → DaemonClient (httpx real)
        → admin app FastAPI real (routing + auth + validación de schemas +
          dispatch_inbound_turn + scope_registry real)
          → run_agent.execute()  ← ÚNICO mock (borde determinista, ver S1)

El único mock está en ``run_agent`` — y es **stateful**, así ``/clear`` es un E2E
genuino: turnos → historial poblado → clear → vacío. Todo lo demás (parsing de
respuestas, serialización, error mapping del cliente, auth del server) es real.

Plumbing: ``DaemonClient`` llama ``httpx.post/get/delete`` a nivel de módulo
(sync). Para rutearlas al app en proceso sin puerto real, se sustituye el módulo
``httpx`` que ve ``daemon_client`` por un shim que delega en un ``TestClient``
(starlette) — manteniendo las clases de excepción reales para los except.

El escenario "daemon caído" NO usa el shim: un ``DaemonClient`` real contra un
puerto cerrado de verdad produce un ``ConnectError`` real.
"""

from __future__ import annotations

import socket
import types

import httpx
import pytest
import typer
from starlette.testclient import TestClient

from adapters.outbound import daemon_client as daemon_client_module
from adapters.outbound.daemon_client import DaemonClient
from adapters.outbound.scope_registry_adapter import InMemoryScopeRegistryAdapter
from core.domain.entities.message import Message, Role
from core.domain.value_objects.agent_info import AgentInfoDTO

_AGENT_ID = "general"
_AUTH_KEY = "e2e-key"


# ---------------------------------------------------------------------------
# Fakes — el ÚNICO borde mockeado es run_agent. Todo lo demás es real.
# ---------------------------------------------------------------------------


class _StatefulFakeRunAgent:
    """Fake stateful de ``RunAgentUseCase`` — solo los métodos que tocan los routers.

    Mantiene un historial en memoria para que ``/clear`` sea un E2E real: cada
    ``execute`` registra el turno (user + assistant), ``get_history`` lo devuelve
    y ``clear_history`` lo vacía.
    """

    def __init__(self, agent_id: str) -> None:
        self._agent_id = agent_id
        self._history: list[Message] = []
        self.execute_calls: list[str] = []

    async def execute(self, message: str, *, intermediate_sink=None, **kwargs) -> str:
        self.execute_calls.append(message)
        reply = f"eco: {message}"
        self._history.append(Message(role=Role.USER, content=message))
        self._history.append(Message(role=Role.ASSISTANT, content=reply))
        return reply

    async def record_user_message(self, message: str, channel: str, chat_id: str) -> None:
        self._history.append(Message(role=Role.USER, content=message))

    async def get_history(self) -> list[Message]:
        return list(self._history)

    async def clear_history(self) -> None:
        self._history.clear()

    def get_agent_info(self) -> AgentInfoDTO:
        return AgentInfoDTO(id=self._agent_id, name="General", description="agente de prueba")


class _FakeAgentConfig:
    """Mínimo contrato que el router de chat consume: ``channels`` (dict)."""

    def __init__(self) -> None:
        self.channels: dict = {}


class _FakeAgentContainer:
    """Expone lo que los routers admin acceden por agente — scope_registry REAL."""

    def __init__(self, agent_id: str) -> None:
        self.run_agent = _StatefulFakeRunAgent(agent_id)
        self.scope_registry = InMemoryScopeRegistryAdapter()
        self.agent_config = _FakeAgentConfig()


class _FakeAppContainer:
    """Root container — solo necesita ``.agents`` para el resolver_agente real."""

    def __init__(self) -> None:
        self.agents = {_AGENT_ID: _FakeAgentContainer(_AGENT_ID)}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app_container() -> _FakeAppContainer:
    return _FakeAppContainer()


@pytest.fixture
def connected_client(app_container, monkeypatch) -> DaemonClient:
    """DaemonClient REAL cuyo httpx se rutea al admin app REAL en proceso.

    Sustituye el módulo ``httpx`` que ve ``daemon_client`` por un shim que delega
    post/get/delete en un ``TestClient`` (mismo event-loop bridge que usa FastAPI
    para apps async) y reexpone las excepciones reales para los except del cliente.
    """
    from adapters.inbound.rest.admin.app import create_admin_app

    app = create_admin_app(app_container, admin_auth_key=_AUTH_KEY)
    test_client = TestClient(app)

    shim = types.SimpleNamespace(
        post=test_client.post,
        get=test_client.get,
        delete=test_client.delete,
        ConnectError=httpx.ConnectError,
        TimeoutException=httpx.TimeoutException,
    )
    monkeypatch.setattr(daemon_client_module, "httpx", shim)

    # base_url irrelevante: el TestClient rutea cualquier host al app en proceso.
    return DaemonClient(admin_base_url="http://daemon-e2e", auth_key=_AUTH_KEY)


def _feed_input(monkeypatch, lines: list[str]) -> None:
    """Monkeypatcha ``builtins.input`` para alimentar el REPL con ``lines``.

    Al agotarse, levanta EOFError → el runner imprime despedida y retorna (red de
    seguridad por si el script no termina con /exit).
    """
    it = iter(lines)

    def fake_input(_prompt: str = "") -> str:
        try:
            return next(it)
        except StopIteration:
            raise EOFError

    monkeypatch.setattr("builtins.input", fake_input)


# ---------------------------------------------------------------------------
# Escenario 1 — Happy path: mensaje → respuesta → salida limpia
# ---------------------------------------------------------------------------


def test_happy_path_message_reply_and_clean_exit(
    connected_client, app_container, monkeypatch, capsys
):
    from adapters.inbound.cli.cli_runner import run_cli

    _feed_input(monkeypatch, ["hola inaki", "/exit"])

    run_cli(connected_client, _AGENT_ID)  # retorna limpio (sin SystemExit)

    out = capsys.readouterr().out
    # La respuesta del agente atravesó: run_cli → DaemonClient → POST /admin/chat/turn
    # → dispatch_inbound_turn → fake execute → ChatTurnResponse → parse del cliente.
    assert "eco: hola inaki" in out
    assert "Hasta luego." in out

    fake = app_container.agents[_AGENT_ID].run_agent
    assert fake.execute_calls == ["hola inaki"]


# ---------------------------------------------------------------------------
# Escenario 2 — /clear end-to-end: historial se puebla, /clear lo vacía
# ---------------------------------------------------------------------------


def test_clear_end_to_end_empties_history(connected_client, monkeypatch, capsys):
    from adapters.inbound.cli.cli_runner import run_cli

    _feed_input(
        monkeypatch,
        [
            "primer turno",
            "segundo turno",
            "/history",  # debe mostrar 4 mensajes (2 user + 2 assistant)
            "/clear",
            "/history",  # debe mostrar "(historial vacío)"
            "/exit",
        ],
    )

    run_cli(connected_client, _AGENT_ID)

    out = capsys.readouterr().out

    # Antes del clear el historial tenía contenido real persistido por execute.
    assert "user: primer turno" in out
    assert "assistant: eco: primer turno" in out
    assert "user: segundo turno" in out

    # /clear viajó como DELETE /admin/chat/history → clear_history real.
    assert "Historial limpiado." in out

    # El /history posterior al clear confirma el efecto end-to-end.
    assert "(historial vacío)" in out


# ---------------------------------------------------------------------------
# Escenario 3 — daemon caído al inicio: exit code != 0 + mensaje accionable
# ---------------------------------------------------------------------------


def test_daemon_down_at_start_exits_nonzero_with_actionable_message(capsys):
    from inaki.cli import _require_daemon

    # Puerto libre y CERRADO: bindeamos para reservarlo, lo soltamos y usamos su
    # número → connect garantiza connection-refused (determinista y rápido, sin
    # depender de timeouts).
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    free_port = s.getsockname()[1]
    s.close()

    # DaemonClient REAL (httpx REAL, sin shim) contra un puerto donde nadie escucha.
    client = DaemonClient(admin_base_url=f"http://127.0.0.1:{free_port}", auth_key=None)

    # _require_daemon llama client.health() → ConnectError real → False → Exit(1).
    with pytest.raises(typer.Exit) as exc_info:
        _require_daemon(client)

    assert exc_info.value.exit_code == 1

    err = capsys.readouterr().err
    assert "El daemon no está corriendo" in err
    assert "inaki daemon" in err  # mensaje accionable para el usuario
