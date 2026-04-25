"""
ChannelsScreen — configuración de canales para un agente.

Muestra y permite editar los canales disponibles:
  - ``telegram``: token (MaskedInput → secrets) + opciones básicas
  - ``telegram.broadcast``: usa BroadcastModeToggle (XOR: off / server / client)
  - ``rest``: host + port

El token de Telegram siempre va a ``agents/{id}.secrets.yaml``.
La validación XOR de broadcast se delega al widget BroadcastModeToggle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, Static

from adapters.inbound.setup_tui.widgets.broadcast_mode_toggle import (
    BroadcastModeToggle,
    BroadcastModo,
)
from adapters.inbound.setup_tui.widgets.diff_preview import DiffPreview
from adapters.inbound.setup_tui.widgets.masked_input import MaskedInput

if TYPE_CHECKING:
    from adapters.inbound.setup_tui.di import SetupContainer

from core.ports.config_repository import LayerName


def _extraer_modo_broadcast(broadcast: dict[str, Any]) -> BroadcastModo:
    """Infiere el modo de broadcast desde el dict de config del agente."""
    if "port" in broadcast and not (broadcast.get("remote") or {}).get("host"):
        return BroadcastModo.SERVER
    if (broadcast.get("remote") or {}).get("host"):
        return BroadcastModo.CLIENT
    return BroadcastModo.DESHABILITADO


class ChannelsScreen(Screen):
    """Pantalla de configuración de canales para un agente."""

    BINDINGS = [
        Binding("ctrl+s", "guardar", "Guardar", show=True),
        Binding("escape", "cancelar", "Cancelar", show=True),
    ]

    def __init__(self, container: "SetupContainer", agent_id: str) -> None:
        super().__init__()
        self._container = container
        self._agent_id = agent_id
        self._datos_capa: dict[str, Any] = {}
        self._datos_secrets: dict[str, Any] = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with ScrollableContainer():
            yield Label(
                f"[bold]Canales — {self._agent_id}[/bold]", markup=True
            )

            # Telegram
            yield Label("[bold]Telegram[/bold]", markup=True)
            yield Label("Token (va a secrets.yaml):")
            yield MaskedInput(id="input-telegram-token")

            # Broadcast
            yield Label("[bold]Broadcast Telegram[/bold]", markup=True)
            yield BroadcastModeToggle(id="broadcast-toggle")

            # REST
            yield Label("[bold]REST[/bold]", markup=True)
            with Vertical():
                yield Label("Host:")
                yield Input(id="input-rest-host")
                yield Label("Port:")
                yield Input(id="input-rest-port")

            yield Label("[bold]Preview de cambios:[/bold]", markup=True)
            yield DiffPreview(id="diff-preview")

            yield Button("Guardar (Ctrl+S)", variant="primary", id="btn-guardar")
            yield Button("Cancelar", variant="default", id="btn-cancelar")
        yield Footer()

    def on_mount(self) -> None:
        self._cargar()

    def _cargar(self) -> None:
        """Carga la config del agente y puebla los campos."""
        self._datos_capa = self._container.repo.read_layer(
            LayerName.AGENT, agent_id=self._agent_id
        )
        self._datos_secrets = self._container.repo.read_layer(
            LayerName.AGENT_SECRETS, agent_id=self._agent_id
        )
        self._poblar_campos()

    def _poblar_campos(self) -> None:
        channels = self._datos_capa.get("channels") or {}
        telegram = channels.get("telegram") or {}
        broadcast = telegram.get("broadcast") or {}
        rest = channels.get("rest") or {}

        # Token de Telegram (desde secrets)
        channels_secrets = self._datos_secrets.get("channels") or {}
        telegram_secrets = channels_secrets.get("telegram") or {}
        token = telegram_secrets.get("token", "")
        try:
            token_widget = self.query_one("#input-telegram-token", MaskedInput)
            token_widget.valor = token
        except Exception:
            pass

        # Broadcast
        modo = _extraer_modo_broadcast(broadcast)
        port_server = broadcast.get("port")
        remote = broadcast.get("remote") or {}
        try:
            toggle = self.query_one("#broadcast-toggle", BroadcastModeToggle)
            toggle._modo_inicial = modo
            toggle._port_server = str(port_server) if port_server else ""
            toggle._remote_host = remote.get("host", "")
            toggle._remote_port = str(remote.get("port", "")) if remote.get("port") else ""
        except Exception:
            pass

        # REST
        try:
            self.query_one("#input-rest-host", Input).value = str(rest.get("host", ""))
            rest_port = rest.get("port")
            self.query_one("#input-rest-port", Input).value = str(rest_port) if rest_port else ""
        except Exception:
            pass

    def _recopilar_cambios(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """
        Retorna (cambios_capa, cambios_secrets).

        El token de Telegram siempre va a secrets.
        """
        cambios_capa: dict[str, Any] = {}
        cambios_secrets: dict[str, Any] = {}

        try:
            toggle = self.query_one("#broadcast-toggle", BroadcastModeToggle)
            modo = toggle.modo
            broadcast_nuevo: dict[str, Any] = {}

            if modo == BroadcastModo.SERVER:
                port = toggle.port_server
                if port:
                    broadcast_nuevo["port"] = port
            elif modo == BroadcastModo.CLIENT:
                remote_host = toggle.remote_host
                remote_port = toggle.remote_port
                if remote_host:
                    remote: dict[str, Any] = {"host": remote_host}
                    if remote_port:
                        remote["port"] = remote_port
                    broadcast_nuevo["remote"] = remote

            # REST
            rest_host = self.query_one("#input-rest-host", Input).value.strip()
            rest_port_str = self.query_one("#input-rest-port", Input).value.strip()
            rest_nuevo: dict[str, Any] = {}
            if rest_host:
                rest_nuevo["host"] = rest_host
            if rest_port_str:
                try:
                    rest_nuevo["port"] = int(rest_port_str)
                except ValueError:
                    pass

            cambios_capa["channels"] = {
                "telegram": {"broadcast": broadcast_nuevo},
                "rest": rest_nuevo,
            }

            # Token a secrets
            token_widget = self.query_one("#input-telegram-token", MaskedInput)
            token = token_widget.valor.strip()
            if token:
                cambios_secrets["channels"] = {"telegram": {"token": token}}

        except Exception:
            pass

        return cambios_capa, cambios_secrets

    async def _guardar(self) -> None:
        cambios_capa, cambios_secrets = self._recopilar_cambios()

        # Diff preview
        yaml_antes = self._container.repo.render_yaml(self._datos_capa)
        datos_nuevos = dict(self._datos_capa)
        datos_nuevos.update(cambios_capa)
        yaml_nuevo = self._container.repo.render_yaml(datos_nuevos)
        diff_widget = self.query_one("#diff-preview", DiffPreview)
        diff_widget.actualizar(yaml_antes, yaml_nuevo, etiqueta=f"agent/{self._agent_id}")

        try:
            if cambios_capa:
                self._container.update_agent_layer.execute(
                    agent_id=self._agent_id,
                    cambios=cambios_capa,
                    layer=LayerName.AGENT,
                )
            if cambios_secrets:
                self._container.update_agent_layer.execute(
                    agent_id=self._agent_id,
                    cambios=cambios_secrets,
                    layer=LayerName.AGENT_SECRETS,
                )
            self.notify("Canales guardados.", title="OK")
            self._datos_capa = datos_nuevos
        except Exception as e:
            self.notify(f"Error al guardar: {e}", severity="error")

    async def action_guardar(self) -> None:
        await self._guardar()

    def action_cancelar(self) -> None:
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-guardar":
            self.run_worker(self._guardar())
        elif event.button.id == "btn-cancelar":
            self.action_cancelar()
