"""Tests para los helpers puros de BasePage.

Foco: _warn_on_invalid_refs — comportamiento ante errores de validación.
No se monta la pantalla en Textual; se testean las rutas lógicas
usando objetos stub que imitan el contenedor y la app.

Los tests de interacción headless con Textual pilot se omiten intencionalmente
porque la combinación de asyncio_mode=auto + pilot requiere compatibilidad con
el event loop de Textual que no se garantiza en este runner.
Ver: https://textual.textualize.io/api/pilot/
"""

from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock, patch

from adapters.inbound.setup_tui.screens._base import BasePage
from adapters.inbound.setup_tui.domain.field import Field
from core.domain.errors import ReferenciaInvalidaError


# ---------------------------------------------------------------------------
# Helpers de construcción
# ---------------------------------------------------------------------------


def _make_field(label: str = "provider", kind: str = "scalar") -> Field:
    return Field(label=label, value="test", kind=kind)  # type: ignore[arg-type]


def _make_page_with_container(container: object) -> BasePage:
    """Crea una BasePage stub con un _container inyectado, sin montar en Textual."""
    page = BasePage.__new__(BasePage)
    page._container = container
    return page


# ---------------------------------------------------------------------------
# _warn_on_invalid_refs
# ---------------------------------------------------------------------------


class TestWarnOnInvalidRefs:
    """_warn_on_invalid_refs notifica correctamente según el tipo de error."""

    def _make_container_with_config(self, config_datos: dict) -> MagicMock:
        """Crea un container mock que retorna datos de config."""
        container = MagicMock()
        efectiva = MagicMock()
        efectiva.datos = config_datos
        container.get_effective_config.execute.return_value = efectiva
        container.list_agents.execute.return_value = ["general"]
        provider_mock = MagicMock()
        provider_mock.key = "openai"
        container.list_providers.execute.return_value = [provider_mock]
        return container

    def test_no_hace_nada_sin_container(self):
        """Sin _container no hay efecto secundario."""
        page = BasePage.__new__(BasePage)
        page._container = None
        # No debe lanzar nada
        page._warn_on_invalid_refs()

    def test_swallow_referencia_invalida_y_notifica_warning(self):
        """ReferenciaInvalidaError → se traga y notifica como warning."""
        container = self._make_container_with_config({})
        page = _make_page_with_container(container)

        app_mock = MagicMock()
        ref_error = ReferenciaInvalidaError(
            campo="app.default_agent",
            valor="inexistente",
            disponibles=["general"],
        )

        # BasePage.app es una property de Textual sin setter.
        # La parcheamos a nivel de clase para este test usando PropertyMock.
        with patch.object(type(page), "app", new_callable=PropertyMock, return_value=app_mock):
            with patch(
                "adapters.inbound.setup_tui.validators.cross_refs.validate_global_config",
                side_effect=ref_error,
            ):
                with patch("infrastructure.config.GlobalConfig"):
                    page._warn_on_invalid_refs()

        # Debe haber llamado a notify con severity="warning"
        app_mock.notify.assert_called_once()
        call_kwargs = app_mock.notify.call_args
        assert call_kwargs.kwargs.get("severity") == "warning"

    def test_swallow_exception_generica_y_notifica_warning(self):
        """Exception genérica (ej. ValidationError de Pydantic) → aviso genérico."""
        container = self._make_container_with_config({})
        page = _make_page_with_container(container)

        app_mock = MagicMock()

        class _FakeError(Exception):
            pass

        with patch.object(type(page), "app", new_callable=PropertyMock, return_value=app_mock):
            with patch(
                "adapters.inbound.setup_tui.validators.cross_refs.validate_global_config",
                side_effect=_FakeError("YAML inválido"),
            ):
                with patch("infrastructure.config.GlobalConfig"):
                    page._warn_on_invalid_refs()

        app_mock.notify.assert_called_once()
        call_kwargs = app_mock.notify.call_args
        assert call_kwargs.kwargs.get("severity") == "warning"

    def test_no_notifica_si_todo_ok(self):
        """Si validate_global_config no lanza, no se notifica nada."""
        container = self._make_container_with_config({})
        page = _make_page_with_container(container)

        app_mock = MagicMock()

        with patch.object(type(page), "app", new_callable=PropertyMock, return_value=app_mock):
            with patch(
                "adapters.inbound.setup_tui.validators.cross_refs.validate_global_config",
                return_value=None,
            ):
                with patch("infrastructure.config.GlobalConfig"):
                    page._warn_on_invalid_refs()

        app_mock.notify.assert_not_called()


# ---------------------------------------------------------------------------
# _after_edit — lógica del escape hatch <null>
# ---------------------------------------------------------------------------


class TestAfterEditNullEscape:
    """_after_edit interpreta '<null>' como None explícito."""

    def _make_page_with_field(self, field: Field) -> BasePage:
        page = BasePage.__new__(BasePage)
        page._cursor_index = 0
        page._fields = [field]
        row_mock = MagicMock()
        row_mock._field = field
        row_mock.refresh_value = MagicMock()
        page._rows = [row_mock]
        page._container = None
        return page

    def test_null_escape_hatch_asigna_none(self):
        field = _make_field()
        page = self._make_page_with_field(field)
        page._after_edit("<null>")
        assert field.value is None

    def test_null_con_espacios_asigna_none(self):
        """'  <null>  ' también se interpreta como None (strip antes de comparar)."""
        field = _make_field()
        page = self._make_page_with_field(field)
        page._after_edit("  <null>  ")
        assert field.value is None

    def test_valor_normal_se_asigna_directo(self):
        field = _make_field()
        page = self._make_page_with_field(field)
        page._after_edit("nuevo_valor")
        assert field.value == "nuevo_valor"

    def test_none_result_no_cambia_valor(self):
        """result is None → el usuario canceló, valor no cambia."""
        field = _make_field(label="provider")
        field.value = "openai"
        page = self._make_page_with_field(field)
        page._after_edit(None)
        assert field.value == "openai"
