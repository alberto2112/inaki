"""``ensamblar`` va dentro de ``borde_de_config``, en el arranque y en cada reload.

El loader no sabe todo: un ``llm.provider`` que no existe o una credencial que
falta los descubre la factory al ensamblar. Ese ``ConfigError`` tiene que salir
como el del loader — mensaje limpio y exit 1 —, no como un traceback (y, en el
reload, no como un exit 0 que systemd no reinicia).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from inaki.app import bootstrap as bootstrap_mod
from inaki.shared.errors import ConfigError


@pytest.fixture
def sin_fs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("inaki.config.ensure_user_channel_dirs", lambda *_a, **_k: None)


def test_arranque_con_ensamblado_rechazado_sale_limpio_con_exit_1(
    sin_fs: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _ensamblar_roto(*_a, **_k):
        raise ConfigError("Proveedor LLM 'nope' no encontrado. Disponibles: ['groq']")

    monkeypatch.setattr("inaki.app.assembly.ensamblar", _ensamblar_roto)

    with pytest.raises(SystemExit) as salida:
        bootstrap_mod.run_daemon_mode(Path("/cfg"), Path("/cfg/agents"), MagicMock(), MagicMock())

    assert salida.value.code == 1
    err = capsys.readouterr().err
    assert "Error de configuración" in err
    assert "Proveedor LLM 'nope'" in err
    assert "Traceback" not in err


def test_reload_con_ensamblado_rechazado_sale_limpio_con_exit_1(
    sin_fs: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    llamadas = {"n": 0}

    def _ensamblar(*_a, **_k):
        # El primero (arranque) anda; el del reload, con la config ya editada, no.
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            return MagicMock(name="inicial")
        raise ConfigError("Provider 'groq' requiere credenciales pero no tiene api_key.")

    monkeypatch.setattr("inaki.app.assembly.ensamblar", _ensamblar)
    monkeypatch.setattr(bootstrap_mod, "bootstrap", lambda *_a: (MagicMock(), MagicMock()))

    async def _run_daemon_que_recarga(bootstrap_fn, initial):
        bootstrap_fn()

    monkeypatch.setattr("inaki.app.runner.run_daemon", _run_daemon_que_recarga)

    with pytest.raises(SystemExit) as salida:
        bootstrap_mod.run_daemon_mode(Path("/cfg"), Path("/cfg/agents"), MagicMock(), MagicMock())

    assert salida.value.code == 1
    err = capsys.readouterr().err
    assert "requiere credenciales" in err
    assert "Traceback" not in err
