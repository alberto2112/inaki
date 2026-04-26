"""
SecretsScreen — vista consolidada de todos los secrets.

Muestra todos los archivos ``*.secrets.yaml`` (global + por agente) en
una sola pantalla. Cada campo aparece con un ``MaskedInput`` con Reveal
individual. La edición escribe al archivo de secrets correcto.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Label, Static

from adapters.inbound.setup_tui.widgets.masked_input import MaskedInput

if TYPE_CHECKING:
    from adapters.inbound.setup_tui.di import SetupContainer

from core.ports.config_repository import LayerName

# Formato de ID interno para cada campo de secret
# "{scope}/{campo_flat}" donde scope es "global" o "agent/{id}"
_SEP = "/"


def _aplanar_dict(datos: dict[str, Any], prefijo: str = "") -> dict[str, Any]:
    """
    Aplana un dict anidado en un dict de rutas punto-separadas.

    Ej: {"a": {"b": 1}} → {"a.b": 1}
    Solo aplana; los valores que no son dict se mantienen como hojas.
    """
    resultado: dict[str, Any] = {}
    for k, v in datos.items():
        ruta = f"{prefijo}.{k}" if prefijo else k
        if isinstance(v, dict):
            resultado.update(_aplanar_dict(v, ruta))
        else:
            resultado[ruta] = v
    return resultado


def _desaplanar_campo(ruta: str, valor: Any) -> dict[str, Any]:
    """
    Convierte una ruta punto-separada y un valor en un dict anidado.

    Ej: ("a.b.c", 1) → {"a": {"b": {"c": 1}}}
    """
    partes = ruta.split(".")
    resultado: dict[str, Any] = {}
    actual = resultado
    for parte in partes[:-1]:
        actual[parte] = {}
        actual = actual[parte]
    actual[partes[-1]] = valor
    return resultado


class _RowSecret(Static):
    """Fila de edición de un campo de secret: etiqueta + MaskedInput."""

    def __init__(
        self,
        scope: str,
        campo_ruta: str,
        valor: Any,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self._scope = scope
        self._campo_ruta = campo_ruta
        self._valor = "" if valor is None else str(valor)

    def compose(self) -> ComposeResult:
        etiqueta = f"[bold]{self._scope}[/bold] → {self._campo_ruta}"
        yield Label(etiqueta, markup=True)
        campo_id = f"secret-{self._scope.replace('/', '-')}-{self._campo_ruta.replace('.', '-')}"
        yield MaskedInput(
            valor=self._valor,
            id=campo_id,
        )


class SecretsScreen(Screen):
    """Vista consolidada de todos los secrets del sistema."""

    BINDINGS = [
        Binding("ctrl+s", "guardar", "Guardar", show=True),
        Binding("escape", "cancelar", "Volver", show=True),
    ]

    def __init__(self, container: "SetupContainer") -> None:
        super().__init__()
        self._container = container
        # Mapeo scope → dict de datos secrets (para diff y guardado)
        self._datos_por_scope: dict[str, dict[str, Any]] = {}
        # Mapeo de id de campo a (scope, campo_ruta)
        self._campos: list[tuple[str, str]] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with ScrollableContainer():
            yield Label("[bold]Secrets[/bold]", markup=True)
            yield Label(
                "[dim]Todos los archivos *.secrets.yaml del sistema.[/dim]",
                markup=True,
            )
            yield Static(id="campos-secrets")
            yield Button("Guardar (Ctrl+S)", variant="primary", id="btn-guardar")
            yield Button("Volver", variant="default", id="btn-volver")
        yield Footer()

    def on_mount(self) -> None:
        self._cargar()

    def _cargar(self) -> None:
        """Carga todos los secrets y arma los campos."""
        self._datos_por_scope = {}
        self._campos = []

        # Global secrets
        global_secrets = self._container.repo.read_layer(LayerName.GLOBAL_SECRETS)
        self._datos_por_scope["global"] = global_secrets

        # Secrets de agentes
        agentes = self._container.list_agents.execute()
        for ag_id in agentes:
            ag_secrets = self._container.repo.read_layer(
                LayerName.AGENT_SECRETS, agent_id=ag_id
            )
            if ag_secrets:
                self._datos_por_scope[f"agent/{ag_id}"] = ag_secrets

        self._poblar_campos()

    def _poblar_campos(self) -> None:
        try:
            contenedor = self.query_one("#campos-secrets", Static)
        except Exception:
            return

        filas: list[Static] = []
        self._campos = []

        for scope, datos in self._datos_por_scope.items():
            campos_planos = _aplanar_dict(datos)
            if campos_planos:
                filas.append(Label(f"[yellow]── {scope} ──[/yellow]", markup=True))
            for campo_ruta, valor in sorted(campos_planos.items()):
                campo_id = f"row-{scope.replace('/', '-')}-{campo_ruta.replace('.', '-')}"
                filas.append(_RowSecret(scope, campo_ruta, valor, id=campo_id))
                self._campos.append((scope, campo_ruta))

        if filas:
            contenedor.mount(*filas)
        else:
            contenedor.mount(Label("[dim]No hay secrets configurados.[/dim]", markup=True))

    async def _guardar(self) -> None:
        """Guarda los cambios en los archivos de secrets correspondientes."""
        cambios_por_scope: dict[str, dict[str, Any]] = {}

        for scope, campo_ruta in self._campos:
            campo_id = f"secret-{scope.replace('/', '-')}-{campo_ruta.replace('.', '-')}"
            try:
                widget = self.query_one(f"#{campo_id}", MaskedInput)
                valor_orig = _aplanar_dict(self._datos_por_scope.get(scope, {})).get(
                    campo_ruta, ""
                )
                nuevo_valor = widget.valor
                if nuevo_valor != str(valor_orig):
                    if scope not in cambios_por_scope:
                        cambios_por_scope[scope] = {}
                    # Anidar el cambio en el dict de scope
                    anidado = _desaplanar_campo(campo_ruta, nuevo_valor)
                    _deep_merge_in_place(cambios_por_scope[scope], anidado)
            except Exception:
                pass

        if not cambios_por_scope:
            self.notify("Sin cambios para guardar.", title="Info")
            return

        try:
            for scope, cambios in cambios_por_scope.items():
                if scope == "global":
                    from core.ports.config_repository import LayerName as LN

                    self._container.update_global_layer.execute(
                        cambios, layer=LN.GLOBAL_SECRETS
                    )
                elif scope.startswith("agent/"):
                    ag_id = scope[len("agent/"):]
                    self._container.update_agent_layer.execute(
                        agent_id=ag_id,
                        cambios=cambios,
                        layer=LayerName.AGENT_SECRETS,
                    )
            self.notify("Secrets guardados.", title="OK")
        except Exception as e:
            self.notify(f"Error al guardar: {e}", severity="error")

    async def action_guardar(self) -> None:
        await self._guardar()

    def action_cancelar(self) -> None:
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-guardar":
            self.run_worker(self._guardar())
        elif event.button.id == "btn-volver":
            self.action_cancelar()


def _deep_merge_in_place(base: dict, override: dict) -> None:
    """Merge recursivo in-place. Override tiene prioridad."""
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge_in_place(base[k], v)
        else:
            base[k] = v
