"""
Helpers de detección y resolución de broadcast ambiguo (UX-decision#3).

Estas funciones son puras — no dependen de estado de pantalla ni widgets.
Se extraen aquí para que puedan ser usadas desde ``AgentEditorScreen``
y testeadas de forma aislada sin montar Textual.
"""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label


# ---------------------------------------------------------------------------
# Funciones puras de detección / resolución
# ---------------------------------------------------------------------------


def detectar_broadcast_ambiguo(datos_agente: dict[str, Any]) -> bool:
    """
    Retorna True si el YAML del agente tiene ambos modos broadcast definidos.

    Estado inválido: ``channels.telegram.broadcast`` tiene TANTO
    ``port`` (modo server) COMO ``remote.host`` (modo client).
    """
    channels = datos_agente.get("channels") or {}
    telegram = channels.get("telegram") or {}
    broadcast = telegram.get("broadcast") or {}

    tiene_port = "port" in broadcast
    tiene_remote_host = "host" in (broadcast.get("remote") or {})
    return tiene_port and tiene_remote_host


def resolver_broadcast_server(datos: dict[str, Any]) -> dict[str, Any]:
    """
    Retorna una copia de ``datos`` con el modo broadcast resuelto a SERVER.

    Elimina la clave ``broadcast.remote`` manteniendo ``broadcast.port``.
    """
    import copy

    resultado = copy.deepcopy(datos)
    try:
        broadcast = resultado["channels"]["telegram"]["broadcast"]
        broadcast.pop("remote", None)
    except (KeyError, TypeError):
        pass
    return resultado


def resolver_broadcast_client(datos: dict[str, Any]) -> dict[str, Any]:
    """
    Retorna una copia de ``datos`` con el modo broadcast resuelto a CLIENT.

    Elimina la clave ``broadcast.port`` manteniendo ``broadcast.remote``.
    """
    import copy

    resultado = copy.deepcopy(datos)
    try:
        broadcast = resultado["channels"]["telegram"]["broadcast"]
        broadcast.pop("port", None)
    except (KeyError, TypeError):
        pass
    return resultado


# ---------------------------------------------------------------------------
# Modal de resolución de broadcast ambiguo (UX-decision#3)
# ---------------------------------------------------------------------------


class _BroadcastAmbiguoModal(ModalScreen[str | None]):
    """
    Modal que aparece cuando el YAML tiene ambos modos broadcast definidos.

    Retorna:
      - ``"server"`` → conservar modo server (eliminar remote).
      - ``"client"`` → conservar modo client (eliminar port).
      - ``None`` → cancelar (volver sin guardar ni modificar).
    """

    CSS = """
    _BroadcastAmbiguoModal {
        align: center middle;
    }
    #dialog {
        width: 65;
        height: auto;
        padding: 2 4;
        border: thick $error 80%;
        background: $surface;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("[bold red]Configuración inválida[/bold red]", markup=True)
            yield Label(
                "Tu config tiene ambos modos broadcast definidos.\n"
                "Esto es estado inválido.\n\n"
                "¿Cuál querés conservar?",
                markup=True,
            )
            yield Button("Server (exponer puerto)", variant="primary", id="btn-server")
            yield Button("Client (conectar a otro)", variant="warning", id="btn-client")
            yield Button("Cancelar", variant="default", id="btn-cancelar")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-server":
            self.dismiss("server")
        elif btn_id == "btn-client":
            self.dismiss("client")
        else:
            self.dismiss(None)
