"""Tests para EditTristateModal y TristateResult."""

from __future__ import annotations

from adapters.inbound.setup_tui.domain.field import Field
from adapters.inbound.setup_tui.modals.tristate import EditTristateModal, TristateResult


def _make_field(
    *,
    label: str = "provider",
    value: str = "openai",
    tristate_state: str | None = "override_value",
) -> Field:
    """Crea un Field triestado de prueba."""
    return Field(
        label=label,
        value=value,
        kind="scalar",
        is_tristate=True,
        tristate_state=tristate_state,  # type: ignore[arg-type]
    )


class TestTristateResult:
    """Validaciones de la dataclass TristateResult."""

    def test_inherit(self) -> None:
        result = TristateResult(mode="inherit")
        assert result.mode == "inherit"
        assert result.value is None

    def test_override_value(self) -> None:
        result = TristateResult(mode="override_value", value="gpt-4o")
        assert result.mode == "override_value"
        assert result.value == "gpt-4o"

    def test_override_null(self) -> None:
        result = TristateResult(mode="override_null")
        assert result.mode == "override_null"
        assert result.value is None

    def test_override_value_sin_value_defaults_a_none(self) -> None:
        result = TristateResult(mode="override_value")
        assert result.value is None


class TestEditTristateModalCompose:
    """Verifica que EditTristateModal se puede instanciar y componer."""

    def test_instancia_con_field(self) -> None:
        field = _make_field()
        modal = EditTristateModal(field)
        assert modal._field is field

    def test_tiene_3_modos_definidos(self) -> None:
        """El modal define exactamente 3 modos en _MODOS."""
        from adapters.inbound.setup_tui.modals.tristate import _MODOS, _MODO_INDEX

        assert len(_MODOS) == 3
        nombres = [m for m, _ in _MODOS]
        assert "inherit" in nombres
        assert "override_value" in nombres
        assert "override_null" in nombres
        # _MODO_INDEX refleja el mismo orden
        assert _MODO_INDEX["inherit"] == 0
        assert _MODO_INDEX["override_value"] == 1
        assert _MODO_INDEX["override_null"] == 2

    def test_instancia_campo_con_estado_inherit(self) -> None:
        field = _make_field(tristate_state="inherit", value="")
        modal = EditTristateModal(field)
        assert modal._field.tristate_state == "inherit"

    def test_instancia_campo_con_estado_override_null(self) -> None:
        field = _make_field(tristate_state="override_null", value="")
        modal = EditTristateModal(field)
        assert modal._field.tristate_state == "override_null"
