"""``inaki reload`` avisa qué cambios de runtime descarta el reload (config-set-efimero)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner


def _invocar(respuesta: dict) -> tuple:
    from inaki.cli import app

    client = MagicMock()
    client.health.return_value = True
    client.daemon_reload.return_value = respuesta
    with (
        patch("inaki.cli._common.build_daemon_client", return_value=(client, MagicMock())),
        patch("inaki.cli._common.resolve_dirs", return_value=(Path("/x"), Path("/y"))),
    ):
        result = CliRunner().invoke(app, ["reload"])
    return result, client


def test_reload_imprime_los_cambios_descartados_antes_de_reiniciar():
    result, client = _invocar(
        {
            "reloading": True,
            "overrides_descartados": ["anacleto: llm.model = 'x'", "global: user.timezone = 'UTC'"],
        }
    )

    assert result.exit_code == 0
    client.daemon_reload.assert_called_once_with()
    lineas = result.output.splitlines()
    assert "Se descartan 2 cambio(s) de runtime:" in lineas[0]
    assert any("anacleto: llm.model = 'x'" in linea for linea in lineas)
    assert any("global: user.timezone = 'UTC'" in linea for linea in lineas)
    assert lineas[-1] == "Daemon reiniciando..."


def test_reload_sin_descartados_no_imprime_el_aviso():
    result, _ = _invocar({"reloading": True, "overrides_descartados": []})

    assert result.exit_code == 0
    assert result.output.strip() == "Daemon reiniciando..."


def test_reload_contra_un_daemon_viejo_sin_el_campo():
    """Un daemon anterior a esta feature no devuelve ``overrides_descartados``."""
    result, _ = _invocar({"reloading": True})

    assert result.exit_code == 0
    assert result.output.strip() == "Daemon reiniciando..."
