"""ProvidersPage — lista y gestión de providers del registry global."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label

from adapters.inbound.setup_tui.domain.field import Field
from adapters.inbound.setup_tui.modals._dialog import dialog_css
from adapters.inbound.setup_tui.screens._base import BasePage
from adapters.inbound.setup_tui.widgets.config_row import ConfigRow
from adapters.inbound.setup_tui.widgets.section_header import SectionHeader

if TYPE_CHECKING:
    from adapters.inbound.setup_tui.di import SetupContainer


# ---------------------------------------------------------------------------
# Modal: editar / crear un provider
# ---------------------------------------------------------------------------


class _EditProviderModal(ModalScreen[dict[str, str | bool] | None]):
    """Modal con 4 campos para crear o editar un provider del registry.

    Cuando ``edit_mode=True``, el campo key está deshabilitado (no se puede
    renombrar un provider existente — habría que clonar + eliminar).

    Retorna un dict con keys ``key``, ``type``, ``base_url``, ``api_key``
    (cualquiera puede estar vacío/None), o ``None`` si el usuario cancela.
    """

    DEFAULT_CSS = (
        dialog_css("_EditProviderModal")
        + """
    _EditProviderModal #dialog {
        width: 78;
        max-height: 30;
    }
    _EditProviderModal #dialog Input {
        margin-top: 0;
        background: #0d0d0d;
        border: tall $primary;
    }
    _EditProviderModal #dialog .campo-label {
        height: 1;
        margin-top: 1;
        color: $text-muted;
        text-style: dim;
    }
    """
    )

    BINDINGS = [
        Binding("escape", "cancel", show=False),
        Binding("ctrl+s", "commit", show=False),
    ]

    def __init__(
        self,
        key: str = "",
        type_val: str = "",
        base_url: str = "",
        edit_mode: bool = False,
    ) -> None:
        super().__init__()
        self._key = key
        self._type_val = type_val
        self._base_url = base_url
        self._edit_mode = edit_mode

    def compose(self) -> ComposeResult:
        titulo = f"editar  {self._key}" if self._edit_mode else "nuevo provider"
        with Vertical(id="dialog"):
            yield Label(titulo, classes="titulo")

            yield Label(
                "key  [dim](nombre del provider)[/dim]" + ("  [dim]— no editable[/dim]" if self._edit_mode else ""),
                classes="campo-label",
            )
            inp_key = Input(value=self._key, id="input_key", disabled=self._edit_mode)
            yield inp_key

            yield Label("type  [dim](groq / openai / ollama / …)[/dim]", classes="campo-label")
            yield Input(value=self._type_val, placeholder="opcional", id="input_type")

            yield Label("base_url  [dim](override del endpoint)[/dim]", classes="campo-label")
            yield Input(value=self._base_url, placeholder="opcional", id="input_base_url")

            yield Label(
                "api_key  [dim](vacío = no modificar)[/dim]",
                classes="campo-label",
            )
            inp_key2 = Input(placeholder="sk-…", password=True, id="input_api_key")
            inp_key2.select_on_focus = False
            yield inp_key2

            yield Label(
                "[bold]ctrl+s[/bold] [dim]guardar[/dim]   "
                "[bold]esc[/bold] [dim]cancelar[/dim]",
                classes="footer",
            )

    def on_mount(self) -> None:
        if self._edit_mode:
            self.query_one("#input_type", Input).focus()
        else:
            self.query_one("#input_key", Input).focus()

    def action_commit(self) -> None:
        key = (
            self._key if self._edit_mode else self.query_one("#input_key", Input).value.strip()
        )
        if not key:
            self.app.notify("la key no puede estar vacía", severity="warning", timeout=2)
            return

        self.dismiss(
            {
                "key": key,
                "type": self.query_one("#input_type", Input).value.strip(),
                "base_url": self.query_one("#input_base_url", Input).value.strip(),
                "api_key": self.query_one("#input_api_key", Input).value.strip(),
            }
        )

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Modal: confirmación de eliminación de provider
# ---------------------------------------------------------------------------


class _ConfirmDeleteProviderModal(ModalScreen[str | None]):
    """Modal de confirmación para eliminar un provider.

    Retorna ``"solo_global"`` para eliminar solo la entrada de global.yaml,
    ``"con_secrets"`` para eliminar la api_key también, o ``None`` para cancelar.
    """

    DEFAULT_CSS = (
        dialog_css("_ConfirmDeleteProviderModal")
        + """
    _ConfirmDeleteProviderModal #dialog {
        width: 70;
    }
    _ConfirmDeleteProviderModal .opcion {
        height: 1;
        margin-top: 1;
        color: $text;
    }
    """
    )

    BINDINGS = [
        Binding("escape", "cancel", show=False),
        Binding("y", "solo_global", show=False),
        Binding("s", "con_secrets", show=False),
    ]

    def __init__(self, key: str, tiene_api_key: bool) -> None:
        super().__init__()
        self._key = key
        self._tiene_api_key = tiene_api_key

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(
                f"eliminar provider  [bold]{self._key}[/bold]",
                classes="titulo",
            )
            yield Label(
                "[bold]y[/bold]  [dim]eliminar entrada de providers (global.yaml)[/dim]",
                classes="opcion",
            )
            if self._tiene_api_key:
                yield Label(
                    "[bold]s[/bold]  [dim]eliminar también la api_key (secrets)[/dim]",
                    classes="opcion",
                )
            yield Label(
                "[bold]esc[/bold] [dim]cancelar[/dim]",
                classes="footer",
            )

    def action_solo_global(self) -> None:
        self.dismiss("solo_global")

    def action_con_secrets(self) -> None:
        self.dismiss("con_secrets")

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# ProvidersPage
# ---------------------------------------------------------------------------


class ProvidersPage(BasePage):
    """Página de gestión del registry de providers.

    Lista todos los providers con su tipo y estado de api_key.
    Enter abre el modal de edición. ``n`` crea uno nuevo, ``delete`` lo elimina.
    """

    BINDINGS = BasePage.BINDINGS + [
        Binding("n", "create_provider", description="nuevo", show=True, priority=True),
        Binding("delete", "delete_provider", description="eliminar", show=True, priority=True),
    ]

    def __init__(self, container: "SetupContainer | None", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._container = container
        # Mapeo field → ProviderInfo para consultar tipo y api_key al editar
        self._field_provider: dict[int, Any] = {}

    def breadcrumb(self) -> str:
        return "inaki / config / providers"

    def compose_body(self) -> ComposeResult:
        from textual.widgets import Label

        from core.use_cases.config.list_providers import ProviderInfo

        providers: list[ProviderInfo] = []
        error_msg: str | None = None
        if self._container is not None:
            try:
                providers = self._container.list_providers.execute()
            except Exception as exc:
                error_msg = f"{type(exc).__name__}: {exc}"

        yield SectionHeader("PROVIDERS")

        if error_msg is not None:
            yield Label(f"  [red]error al leer providers: {error_msg}[/red]", markup=True)
            yield Label(
                "  [dim]Corregí global.yaml a mano y volvé a abrir esta pantalla.[/dim]",
                markup=True,
            )
            return

        if not providers:
            yield ConfigRow(
                Field(
                    label="(sin providers)",
                    value="→ presioná n para agregar uno",
                    kind="scalar",
                )
            )
        else:
            for provider in providers:
                api_key_indicator = "✓" if provider.tiene_api_key else "—"
                value_str = (
                    f"{provider.type or '—'}  ·  api_key: {api_key_indicator}"
                )
                field = Field(
                    label=provider.key,
                    value=value_str,
                    kind="scalar",
                )
                self._field_provider[id(field)] = provider
                yield ConfigRow(field)

    def action_edit(self) -> None:
        """Abre el modal de edición del provider seleccionado."""
        if not self._fields:
            return

        field = self._current_field()
        if field.label == "(sin providers)":
            return

        provider_info = self._field_provider.get(id(field))

        self.app.push_screen(
            _EditProviderModal(
                key=field.label,
                type_val=getattr(provider_info, "type", "") or "",
                base_url=getattr(provider_info, "base_url", "") or "",
                edit_mode=True,
            ),
            self._after_edit_provider,
        )

    def _after_edit_provider(self, datos: dict[str, Any] | None) -> None:
        if datos is None or self._container is None:
            return

        self._upsert_provider(datos)

    def action_create_provider(self) -> None:
        """Abre el modal de creación de un provider nuevo."""
        self.app.push_screen(
            _EditProviderModal(edit_mode=False),
            self._after_create_provider,
        )

    def _after_create_provider(self, datos: dict[str, Any] | None) -> None:
        if datos is None or self._container is None:
            return

        self._upsert_provider(datos)

    def _upsert_provider(self, datos: dict[str, Any]) -> None:
        """Llama al use case de upsert con los datos del modal."""
        if self._container is None:
            return

        try:
            self._container.upsert_provider.execute(
                key=datos["key"],
                type=datos.get("type") or None,
                base_url=datos.get("base_url") or None,
                api_key=datos.get("api_key") or None,
            )
            self.app.notify(
                f"provider '{datos['key']}' guardado",
                title="providers",
                timeout=2,
            )
        except Exception as exc:
            self.app.notify(str(exc), title="error al guardar", severity="error", timeout=4)
            return

        self._reload()

    def action_delete_provider(self) -> None:
        """Solicita confirmación y elimina el provider seleccionado."""
        if not self._fields:
            return

        field = self._current_field()
        if field.label == "(sin providers)":
            return

        provider_info = self._field_provider.get(id(field))
        tiene_api_key = getattr(provider_info, "tiene_api_key", False)

        self.app.push_screen(
            _ConfirmDeleteProviderModal(field.label, tiene_api_key),
            lambda resultado: self._after_delete(field.label, resultado),
        )

    def _after_delete(self, key: str, resultado: str | None) -> None:
        if resultado is None or self._container is None:
            return

        borrar_api_key = resultado == "con_secrets"

        try:
            self._container.delete_provider.execute(key, borrar_api_key=borrar_api_key)
            self.app.notify(
                f"provider '{key}' eliminado",
                title="providers",
                timeout=2,
            )
        except Exception as exc:
            self.app.notify(str(exc), title="error al eliminar", severity="error", timeout=4)
            return

        self._reload()

    def _reload(self) -> None:
        """Refresca la pantalla reemplazando por una instancia nueva."""
        self.app.pop_screen()
        self.app.push_screen(ProvidersPage(self._container))
