"""
BroadcastModeToggle — selector XOR entre modos de broadcast de Telegram.

Según el spec (setup-tui#channels-xor), el campo ``channels.telegram.broadcast``
admite exactamente UN modo activo:
  - ``DESHABILITADO``: no hay broadcast.
  - ``SERVER``: el agente expone un puerto TCP (``broadcast.port``).
  - ``CLIENT``: el agente se conecta a otro agente (``broadcast.remote.host``).

Solo uno puede estar activo a la vez. El widget garantiza este invariante:
activar un modo desactiva automáticamente el otro.

Emite ``BroadcastModeToggle.Changed`` cuando el modo cambia.
Expone el grupo de campos relevante (server o client) para que la pantalla
los muestre u oculte.
"""

from __future__ import annotations

from enum import Enum

from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Button, Input, Label, Static


class BroadcastModo(str, Enum):
    """Modo de broadcast del agente."""

    DESHABILITADO = "off"
    SERVER = "server"
    CLIENT = "client"


_LABELS: dict[BroadcastModo, str] = {
    BroadcastModo.DESHABILITADO: "Deshabilitado",
    BroadcastModo.SERVER: "Server (expone puerto)",
    BroadcastModo.CLIENT: "Client (conecta a otro)",
}


class BroadcastModeToggle(Static):
    """
    Widget de selección exclusiva (radio) para el modo de broadcast.

    Muestra el grupo de campos relevante según el modo activo:
      - DESHABILITADO: sin campos extra.
      - SERVER: campo ``port`` (int).
      - CLIENT: campo ``host`` + ``port`` del remoto.

    Emite ``BroadcastModeToggle.Changed`` cuando el modo o un campo cambia.
    """

    BINDINGS = [
        Binding("s", "activar_server", "Server", show=False),
        Binding("c", "activar_client", "Client", show=False),
        Binding("d", "desactivar", "Deshabilitar", show=False),
    ]

    modo: reactive[BroadcastModo] = reactive(BroadcastModo.DESHABILITADO)

    class Changed(Message):
        """El modo de broadcast o un campo cambió."""

        def __init__(
            self,
            widget: "BroadcastModeToggle",
            modo: BroadcastModo,
        ) -> None:
            super().__init__()
            self.widget = widget
            self.modo = modo

    def __init__(
        self,
        modo_inicial: BroadcastModo = BroadcastModo.DESHABILITADO,
        port_server: int | None = None,
        remote_host: str | None = None,
        remote_port: int | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._modo_inicial = modo_inicial
        self._port_server: str = str(port_server) if port_server else ""
        self._remote_host: str = remote_host or ""
        self._remote_port: str = str(remote_port) if remote_port else ""

    # ------------------------------------------------------------------
    # Acceso a los valores actuales (para que la pantalla los lea al guardar)
    # ------------------------------------------------------------------

    @property
    def port_server(self) -> int | None:
        """Puerto del modo server. None si el campo está vacío o no aplica."""
        try:
            return int(self.query_one("#input-server-port", Input).value)
        except (ValueError, Exception):
            return None

    @property
    def remote_host(self) -> str | None:
        """Host remoto en modo client."""
        try:
            val = self.query_one("#input-client-host", Input).value.strip()
            return val if val else None
        except Exception:
            return None

    @property
    def remote_port(self) -> int | None:
        """Puerto remoto en modo client."""
        try:
            return int(self.query_one("#input-client-port", Input).value)
        except (ValueError, Exception):
            return None

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        # Botones de modo
        for modo in BroadcastModo:
            variante = "primary" if modo == self._modo_inicial else "default"
            yield Button(_LABELS[modo], variant=variante, id=f"btn-modo-{modo.value}")

        # Campos del modo server
        with Static(id="grupo-server", classes="hidden"):
            yield Label("Puerto (broadcast.port):")
            yield Input(
                value=self._port_server,
                placeholder="ej: 9876",
                id="input-server-port",
            )

        # Campos del modo client
        with Static(id="grupo-client", classes="hidden"):
            yield Label("Host remoto (broadcast.remote.host):")
            yield Input(
                value=self._remote_host,
                placeholder="ej: 192.168.1.100",
                id="input-client-host",
            )
            yield Label("Puerto remoto (broadcast.remote.port):")
            yield Input(
                value=self._remote_port,
                placeholder="ej: 9876",
                id="input-client-port",
            )

    def on_mount(self) -> None:
        self.modo = self._modo_inicial
        self._actualizar_ui()

    # ------------------------------------------------------------------
    # Eventos
    # ------------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        btn_id = event.button.id or ""
        for modo in BroadcastModo:
            if btn_id == f"btn-modo-{modo.value}":
                self.modo = modo
                break

    def on_input_changed(self, _event: Input.Changed) -> None:
        self.post_message(self.Changed(self, self.modo))

    # ------------------------------------------------------------------
    # Acciones de teclado
    # ------------------------------------------------------------------

    def action_activar_server(self) -> None:
        self.modo = BroadcastModo.SERVER

    def action_activar_client(self) -> None:
        self.modo = BroadcastModo.CLIENT

    def action_desactivar(self) -> None:
        self.modo = BroadcastModo.DESHABILITADO

    # ------------------------------------------------------------------
    # Reactive
    # ------------------------------------------------------------------

    def watch_modo(self, nuevo: BroadcastModo) -> None:
        self._actualizar_ui()
        self.post_message(self.Changed(self, nuevo))

    def _actualizar_ui(self) -> None:
        """Muestra el grupo de campos del modo activo y oculta los demás."""
        try:
            # Actualizar variante de botones
            for modo in BroadcastModo:
                btn = self.query_one(f"#btn-modo-{modo.value}", Button)
                btn.variant = "primary" if modo == self.modo else "default"

            # Mostrar/ocultar grupos
            grupo_server = self.query_one("#grupo-server", Static)
            grupo_client = self.query_one("#grupo-client", Static)

            if self.modo == BroadcastModo.SERVER:
                grupo_server.remove_class("hidden")
                grupo_client.add_class("hidden")
            elif self.modo == BroadcastModo.CLIENT:
                grupo_server.add_class("hidden")
                grupo_client.remove_class("hidden")
            else:
                grupo_server.add_class("hidden")
                grupo_client.add_class("hidden")
        except Exception:
            pass  # Aún no montado
