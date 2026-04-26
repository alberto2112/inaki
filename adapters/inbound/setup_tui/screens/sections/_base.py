"""
SectionEditorScreen — base genérica para subpantallas de edición por sección.

Cada sección de la config (llm, embedding, memory, etc.) tiene su propia
subclase que solo declara ``SECTION_KEY``, ``TITULO`` y ``CAMPOS``.
La lógica de render, tri-estado, diff preview y guardado vive aquí.

Modos:
  - ``layer=GLOBAL, override_mode=False``: edita la sección en global.yaml.
  - ``layer=AGENT, override_mode=True``: edita el override de la sección
    en agents/{agent_id}.yaml. Cada campo tiene UI de Heredar / Valor propio.
    Los campos con ``es_tristate=True`` usan ``TristateToggle`` (3 estados).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer
from textual.message import Message
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, Static

from adapters.inbound.setup_tui.widgets.diff_preview import DiffPreview
from adapters.inbound.setup_tui.widgets.layer_label import LayerLabel
from adapters.inbound.setup_tui.widgets.tristate_toggle import TristateToggle, TristateValorUI

if TYPE_CHECKING:
    from adapters.inbound.setup_tui.di import SetupContainer

from core.ports.config_repository import LayerName
from core.use_cases.config.update_agent_layer import CampoTriestado, TristadoValor


# ---------------------------------------------------------------------------
# FieldSpec — descriptor de un campo editable
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldSpec:
    """Descriptor de un campo editable dentro de una sección de config."""

    key: str
    """Clave YAML dentro de la sección (p. ej. ``"model"`` en ``llm``)."""

    tipo: type
    """Tipo Python del valor: ``str``, ``int``, ``float``, ``bool``."""

    descripcion: str = ""
    """Hint mostrado al usuario bajo el input."""

    enum_choices: tuple[str, ...] | None = None
    """Si se setea, el campo usa un select con estas opciones fijas."""

    dropdown_source: str | None = None
    """``"agents"`` o ``"providers"`` — lista dinámica resuelta en runtime."""

    placeholder: str = ""
    """Placeholder del Input cuando el campo está vacío."""

    es_tristate: bool = False
    """
    Si ``True`` y la pantalla está en ``override_mode``, usa ``TristateToggle``
    en lugar del toggle simple Heredar/Valor propio.
    Solo aplica en mode override (capas de agente).
    """

    es_nullable: bool = False
    """Si ``True``, el campo puede guardar ``null`` explícito (fuera de tristate)."""

    es_lista: bool = False
    """
    Si ``True``, el campo es ``list[str]``. La UI renderiza un Input que
    parsea/serializa como CSV (``"a, b, c"`` ↔ ``["a", "b", "c"]``).
    Una lista vacía no se considera cambio respecto a ``None`` ausente
    para evitar escrituras espurias al YAML.
    """


# ---------------------------------------------------------------------------
# SectionEditorScreen — base
# ---------------------------------------------------------------------------


class SectionEditorScreen(Screen):
    """
    Pantalla genérica de edición de sección de config.

    Subclases concretas deben declarar:
      - ``SECTION_KEY`` — clave top-level en el YAML (p. ej. ``"llm"``)
      - ``TITULO``     — título mostrado en el header (p. ej. ``"LLM"``)
      - ``CAMPOS``     — tupla de ``FieldSpec`` con los campos editables
    """

    SECTION_KEY: ClassVar[str] = ""
    TITULO: ClassVar[str] = ""
    CAMPOS: ClassVar[tuple[FieldSpec, ...]] = ()

    BINDINGS = [
        Binding("ctrl+s", "guardar", "Guardar", show=True),
        Binding("escape", "cancelar", "Cancelar", show=True),
    ]

    def __init__(
        self,
        container: "SetupContainer",
        layer: LayerName,
        agent_id: str | None = None,
        override_mode: bool = False,
    ) -> None:
        super().__init__()
        self._container = container
        self._layer = layer
        self._agent_id = agent_id
        self._override_mode = override_mode
        # Valores de la sección tal como están en disco (solo la capa)
        self._valores_capa: dict[str, Any] = {}
        # Snapshot YAML antes de editar (para diff)
        self._yaml_antes: str = ""

    # ------------------------------------------------------------------
    # compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        agente_txt = f" — {self._agent_id}" if self._agent_id else ""
        yield Header(show_clock=False)
        with ScrollableContainer():
            yield Label(
                f"[bold]{self.TITULO}{agente_txt}[/bold]",
                markup=True,
            )
            if self._override_mode:
                yield Label(
                    "[dim]Modo override: podés heredar del global o definir un valor propio.[/dim]",
                    markup=True,
                )
            yield Static(id="campos-seccion")
            yield Label("[bold]Preview de cambios:[/bold]", markup=True)
            yield DiffPreview(id="diff-preview")
            with Horizontal():
                yield Button("Guardar (Ctrl+S)", variant="primary", id="btn-guardar")
                yield Button("Cancelar", variant="default", id="btn-cancelar")
        yield Footer()

    # ------------------------------------------------------------------
    # mount / cargar
    # ------------------------------------------------------------------

    def on_mount(self) -> None:
        self._cargar()

    def _cargar(self) -> None:
        """Lee la capa de disco y puebla los campos."""
        datos_capa = self._container.repo.read_layer(
            self._layer, agent_id=self._agent_id
        )
        self._valores_capa = (datos_capa.get(self.SECTION_KEY) or {}).copy()
        self._yaml_antes = self._container.repo.render_yaml(
            {self.SECTION_KEY: self._valores_capa}
        )
        self._poblar_campos()

    def _poblar_campos(self) -> None:
        try:
            contenedor = self.query_one("#campos-seccion", Static)
        except Exception:
            return

        widgets: list = []
        agentes_disponibles = self._container.list_agents.execute() if any(
            f.dropdown_source == "agents" for f in self.CAMPOS
        ) else []
        providers_disponibles = [p.key for p in self._container.list_providers.execute()] if any(
            f.dropdown_source == "providers" for f in self.CAMPOS
        ) else []

        for spec in self.CAMPOS:
            valor_actual = self._valores_capa.get(spec.key)
            widgets.extend(
                self._widgets_para_campo(spec, valor_actual, agentes_disponibles, providers_disponibles)
            )

        if widgets:
            contenedor.mount(*widgets)

    def _widgets_para_campo(
        self,
        spec: FieldSpec,
        valor_actual: Any,
        agentes: list[str],
        providers: list[str],
    ) -> list:
        """Crea la fila de widgets para un campo."""
        hint = f" [dim]({spec.descripcion})[/dim]" if spec.descripcion else ""
        label_txt = f"[bold]{spec.key}[/bold]{hint}"

        widgets: list = []
        widgets.append(Label(label_txt, markup=True))

        # Modo override: toggle de herencia + input (o tristate)
        if self._override_mode:
            campo_presente = spec.key in self._valores_capa

            if spec.es_tristate:
                # TristateToggle para campos como memory.llm.*
                if not campo_presente:
                    estado_inicial = TristateValorUI.INHERIT
                elif valor_actual is None:
                    estado_inicial = TristateValorUI.OVERRIDE_NULL
                else:
                    estado_inicial = TristateValorUI.OVERRIDE_VALUE
                widgets.append(TristateToggle(estado_inicial=estado_inicial, id=f"tristate-{spec.key}"))
                inp = Input(
                    value=str(valor_actual) if valor_actual is not None else "",
                    placeholder=spec.placeholder,
                    id=f"input-{spec.key}",
                    disabled=(estado_inicial != TristateValorUI.OVERRIDE_VALUE),
                )
                widgets.append(inp)
            else:
                # Toggle simple Heredar / Valor propio
                widgets.append(
                    _HerenciaToggle(
                        override=campo_presente,
                        id=f"herencia-{spec.key}",
                    )
                )
                inp = self._input_para_campo(
                    spec, valor_actual, agentes, providers,
                    disabled=not campo_presente,
                )
                widgets.append(inp)
        else:
            # Modo global: input directo
            inp = self._input_para_campo(spec, valor_actual, agentes, providers)
            widgets.append(inp)

        if self._layer in (LayerName.GLOBAL, LayerName.GLOBAL_SECRETS):
            widgets.append(LayerLabel(LayerName.GLOBAL.value))
        elif self._agent_id:
            widgets.append(LayerLabel(LayerName.AGENT.value, agent_id=self._agent_id))

        return widgets

    def _input_para_campo(
        self,
        spec: FieldSpec,
        valor_actual: Any,
        agentes: list[str],
        providers: list[str],
        disabled: bool = False,
    ) -> Input:
        """Crea el widget Input apropiado para un campo."""
        valor_str = ""
        if spec.es_lista:
            if isinstance(valor_actual, list):
                valor_str = ", ".join(str(x) for x in valor_actual)
        elif valor_actual is not None:
            valor_str = str(valor_actual)
        elif spec.tipo is bool:
            valor_str = "false"

        placeholder = spec.placeholder
        if spec.es_lista and not placeholder:
            placeholder = "valor1, valor2, valor3"

        return Input(
            value=valor_str,
            placeholder=placeholder or f"Valor para {spec.key}",
            id=f"input-{spec.key}",
            disabled=disabled,
        )

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def on_tristate_toggle_changed(self, event: TristateToggle.Changed) -> None:
        """Habilita/deshabilita el input cuando el tristate cambia."""
        toggle_id = event.widget.id or ""
        campo = toggle_id.replace("tristate-", "")
        try:
            inp = self.query_one(f"#input-{campo}", Input)
            inp.disabled = event.estado != TristateValorUI.OVERRIDE_VALUE
        except Exception:
            pass

    def on__herencia_toggle_changed(self, event: "_HerenciaToggle.Changed") -> None:
        """Habilita/deshabilita el input cuando el toggle heredar/override cambia."""
        toggle_id = event.widget.id or ""
        campo = toggle_id.replace("herencia-", "")
        try:
            inp = self.query_one(f"#input-{campo}", Input)
            inp.disabled = not event.override
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Recopilar cambios
    # ------------------------------------------------------------------

    def _recopilar_cambios(self) -> dict[str, Any]:
        """
        Lee los widgets y construye el dict de cambios para el section_key.

        En modo override, un campo en INHERIT no aparece en el resultado.
        En modo tristate, devuelve ``CampoTriestado``.
        """
        cambios_seccion: dict[str, Any] = {}

        for spec in self.CAMPOS:
            if self._override_mode:
                if spec.es_tristate:
                    try:
                        toggle = self.query_one(f"#tristate-{spec.key}", TristateToggle)
                        inp = self.query_one(f"#input-{spec.key}", Input)
                        if toggle.estado == TristateValorUI.INHERIT:
                            cambios_seccion[spec.key] = CampoTriestado(TristadoValor.INHERIT)
                        elif toggle.estado == TristateValorUI.OVERRIDE_NULL:
                            cambios_seccion[spec.key] = CampoTriestado(TristadoValor.OVERRIDE_NULL)
                        else:
                            cambios_seccion[spec.key] = CampoTriestado(
                                TristadoValor.OVERRIDE_VALOR, _convertir_tipo(inp.value, spec)
                            )
                    except Exception:
                        pass
                else:
                    try:
                        herencia = self.query_one(f"#herencia-{spec.key}", _HerenciaToggle)
                        inp = self.query_one(f"#input-{spec.key}", Input)
                        if herencia.override:
                            cambios_seccion[spec.key] = _convertir_tipo(inp.value, spec)
                        # Si INHERIT, no agregamos — el use case que llame update_agent_layer
                        # solo escribe los campos presentes.
                    except Exception:
                        pass
            else:
                try:
                    inp = self.query_one(f"#input-{spec.key}", Input)
                    valor_nuevo = _convertir_tipo(inp.value, spec)
                    valor_original = self._valores_capa.get(spec.key)
                    # Lista vacía no se considera cambio si el original era None ausente
                    if spec.es_lista and not valor_nuevo and valor_original in (None, []):
                        continue
                    if valor_nuevo != valor_original:
                        cambios_seccion[spec.key] = valor_nuevo
                except Exception:
                    pass

        return cambios_seccion

    # ------------------------------------------------------------------
    # Guardar
    # ------------------------------------------------------------------

    async def _guardar(self) -> None:
        cambios_seccion = self._recopilar_cambios()

        if not cambios_seccion:
            self.notify("Sin cambios para guardar.", title="Info")
            return

        cambios = {self.SECTION_KEY: cambios_seccion}

        # Diff preview
        yaml_despues = self._container.repo.render_yaml(
            {self.SECTION_KEY: {**self._valores_capa, **{
                k: (v.valor if isinstance(v, CampoTriestado) and v.modo.value == "valor" else v)
                for k, v in cambios_seccion.items()
                if not isinstance(v, CampoTriestado) or v.modo.value != "inherit"
            }}}
        )
        diff_widget = self.query_one("#diff-preview", DiffPreview)
        diff_widget.actualizar(self._yaml_antes, yaml_despues, etiqueta=self.SECTION_KEY)

        try:
            if self._layer == LayerName.GLOBAL:
                self._container.update_global_layer.execute(cambios, layer=LayerName.GLOBAL)
            else:
                self._container.update_agent_layer.execute(
                    agent_id=self._agent_id or "",
                    cambios=cambios,
                    layer=self._layer,
                )
            # Actualizar snapshot
            self._yaml_antes = yaml_despues
            # Refrescar valores_capa para no-tristate
            for k, v in cambios_seccion.items():
                if not isinstance(v, CampoTriestado):
                    self._valores_capa[k] = v
            self.notify(f"Sección '{self.SECTION_KEY}' guardada.", title="OK")
        except Exception as e:
            self.notify(f"Error al guardar: {e}", title="Error", severity="error")

    # ------------------------------------------------------------------
    # Actions / buttons
    # ------------------------------------------------------------------

    async def action_guardar(self) -> None:
        await self._guardar()

    def action_cancelar(self) -> None:
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-guardar":
            self.run_worker(self._guardar())
        elif event.button.id == "btn-cancelar":
            self.action_cancelar()


# ---------------------------------------------------------------------------
# _HerenciaToggle — toggle simple Heredar / Valor propio (sin null)
# ---------------------------------------------------------------------------


class _HerenciaToggle(Static):
    """
    Toggle de dos estados: Heredar (valor del global) vs Valor propio.

    Emite ``_HerenciaToggle.Changed`` cuando cambia el estado.
    """

    override: reactive[bool] = reactive(False)

    class Changed(Message):
        def __init__(self, widget: "_HerenciaToggle", override: bool) -> None:
            super().__init__()
            self.widget = widget
            self.override = override

    def __init__(
        self,
        override: bool = False,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._override_inicial = override

    def compose(self) -> ComposeResult:
        var = "primary" if self._override_inicial else "default"
        no_var = "default" if self._override_inicial else "primary"
        yield Button("Heredar", variant=no_var, id="btn-heredar")
        yield Button("Valor propio", variant=var, id="btn-override")

    def on_mount(self) -> None:
        self.override = self._override_inicial

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "btn-heredar":
            self.override = False
        elif event.button.id == "btn-override":
            self.override = True

    def watch_override(self, nuevo: bool) -> None:
        self._actualizar_botones()
        self.post_message(self.Changed(self, nuevo))

    def _actualizar_botones(self) -> None:
        try:
            self.query_one("#btn-heredar", Button).variant = "default" if self.override else "primary"
            self.query_one("#btn-override", Button).variant = "primary" if self.override else "default"
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Helpers de conversión de tipo
# ---------------------------------------------------------------------------


def _convertir_tipo(valor_str: str, spec: FieldSpec) -> Any:
    """Convierte el string de un Input al tipo Python del FieldSpec."""
    if spec.es_lista:
        return [s.strip() for s in valor_str.split(",") if s.strip()]
    if spec.tipo is bool:
        return valor_str.strip().lower() in ("true", "1", "yes", "sí", "si")
    if spec.tipo is int:
        try:
            return int(valor_str.strip())
        except (ValueError, TypeError):
            return valor_str
    if spec.tipo is float:
        try:
            return float(valor_str.strip())
        except (ValueError, TypeError):
            return valor_str
    return valor_str
