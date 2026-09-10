"""``inaki service install|uninstall``: render puro, camino sin root y camino con root."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from inaki.cli import service
from inaki.cli.service import render_unit, service_app
from inaki.config.home import set_inaki_home


@pytest.fixture
def home(tmp_path: Path):
    set_inaki_home(tmp_path / "inaki-home")
    yield tmp_path / "inaki-home"
    set_inaki_home(None)


@pytest.fixture
def entorno(home: Path):
    """Usuario, grupo y ejecutable fijos: lo que varía por máquina no entra al test."""
    with (
        patch.object(service, "_usuario_y_grupo", return_value=("pi", "pi")),
        patch.object(service, "ruta_del_cli", return_value=Path("/opt/venv/bin/inaki")),
    ):
        yield


def test_render_unit_apunta_al_ejecutable_absoluto_y_al_home() -> None:
    unidad = render_unit(
        user="pi", group="pi", exec_start=Path("/opt/venv/bin/inaki"), home=Path("/srv/inaki")
    )
    assert "ExecStart=/opt/venv/bin/inaki daemon\n" in unidad
    assert "Environment=INAKI_HOME=/srv/inaki\n" in unidad
    assert "WorkingDirectory=/srv/inaki\n" in unidad
    assert "User=pi\nGroup=pi\n" in unidad
    assert "WantedBy=multi-user.target" in unidad


def test_print_muestra_la_unidad_sin_escribir_nada(home: Path, entorno) -> None:
    result = CliRunner().invoke(service_app, ["install", "--print"])

    assert result.exit_code == 0, result.output
    assert "ExecStart=/opt/venv/bin/inaki daemon" in result.output
    assert not home.exists()


def test_sin_root_deja_la_unidad_en_el_home_y_dicta_los_sudo(home: Path, entorno) -> None:
    with patch.object(service, "_es_root", return_value=False):
        result = CliRunner().invoke(service_app, ["install", "--link-cli"])

    assert result.exit_code == 0, result.output
    borrador = home / "inaki.service"
    assert borrador.exists()
    assert "ExecStart=/opt/venv/bin/inaki daemon" in borrador.read_text()
    assert f"sudo cp {borrador} /etc/systemd/system/inaki.service" in result.output
    assert "sudo systemctl enable --now inaki" in result.output
    assert "sudo ln -sfn /opt/venv/bin/inaki /usr/local/bin/inaki" in result.output
    assert "sudo /opt/venv/bin/inaki service install --link-cli" in result.output


def test_con_root_escribe_la_unidad_y_la_habilita(home: Path, entorno, tmp_path: Path) -> None:
    destino = tmp_path / "etc" / "inaki.service"
    destino.parent.mkdir()
    systemctl = MagicMock()
    with (
        patch.object(service, "_es_root", return_value=True),
        patch.object(service, "RUTA_UNIDAD", destino),
        patch.object(service, "_systemctl", systemctl),
    ):
        result = CliRunner().invoke(service_app, ["install"])

    assert result.exit_code == 0, result.output
    assert "ExecStart=/opt/venv/bin/inaki daemon" in destino.read_text()
    assert oct(destino.stat().st_mode & 0o777) == "0o644"
    assert [c.args for c in systemctl.call_args_list] == [
        ("daemon-reload",),
        ("enable", "inaki"),
        ("restart", "inaki"),
    ]
    assert "Servicio instalado" in result.output


def test_sin_link_cli_no_toca_usr_local_bin(home: Path, entorno, tmp_path: Path) -> None:
    enlace = tmp_path / "usr-local-bin" / "inaki"
    with (
        patch.object(service, "_es_root", return_value=True),
        patch.object(service, "RUTA_UNIDAD", tmp_path / "inaki.service"),
        patch.object(service, "ENLACE_CLI", enlace),
        patch.object(service, "_systemctl", MagicMock()),
    ):
        CliRunner().invoke(service_app, ["install"])
        assert not enlace.exists()
        CliRunner().invoke(service_app, ["install", "--link-cli"])
        assert enlace.is_symlink() and os.readlink(enlace) == "/opt/venv/bin/inaki"


def test_uninstall_con_root_deshabilita_y_borra(home: Path, tmp_path: Path) -> None:
    unidad = tmp_path / "inaki.service"
    unidad.write_text("x")
    systemctl = MagicMock()
    with (
        patch.object(service, "_es_root", return_value=True),
        patch.object(service, "RUTA_UNIDAD", unidad),
        patch.object(service, "ENLACE_CLI", tmp_path / "no-existe"),
        patch.object(service, "_systemctl", systemctl),
    ):
        result = CliRunner().invoke(service_app, ["uninstall"])

    assert result.exit_code == 0, result.output
    assert not unidad.exists()
    assert [c.args for c in systemctl.call_args_list] == [
        ("disable", "--now", "inaki"),
        ("daemon-reload",),
    ]


def test_uninstall_sin_root_dicta_los_comandos(home: Path) -> None:
    with patch.object(service, "_es_root", return_value=False):
        result = CliRunner().invoke(service_app, ["uninstall"])

    assert result.exit_code == 0, result.output
    assert "sudo systemctl disable --now inaki" in result.output
