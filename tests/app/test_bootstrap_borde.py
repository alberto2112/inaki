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
    monkeypatch.setattr(bootstrap_mod, "bootstrap", lambda *_a, **_k: (MagicMock(), MagicMock()))

    async def _run_daemon_que_recarga(bootstrap_fn, initial, **_k):
        bootstrap_fn()

    monkeypatch.setattr("inaki.app.runner.run_daemon", _run_daemon_que_recarga)

    with pytest.raises(SystemExit) as salida:
        bootstrap_mod.run_daemon_mode(Path("/cfg"), Path("/cfg/agents"), MagicMock(), MagicMock())

    assert salida.value.code == 1
    err = capsys.readouterr().err
    assert "requiere credenciales" in err
    assert "Traceback" not in err


def test_un_unico_store_viaja_al_arranque_y_a_cada_reload(
    sin_fs: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El store es estado del PROCESO: nace en ``run_daemon_mode`` y sobrevive al loop.

    El arranque y el re-bootstrap tienen que ver el MISMO objeto, si no un ``config_set``
    aceptado antes del reload se perdería sin que nadie lo descarte.
    """
    vistos: dict = {"ensamblar": [], "bootstrap": []}

    def _ensamblar(*_a, overrides=None, **_k):
        vistos["ensamblar"].append(overrides)
        return MagicMock(name="runtime")

    def _bootstrap(*_a, overrides=None, **_k):
        vistos["bootstrap"].append(overrides)
        return MagicMock(), MagicMock()

    monkeypatch.setattr("inaki.app.assembly.ensamblar", _ensamblar)
    monkeypatch.setattr(bootstrap_mod, "bootstrap", _bootstrap)

    async def _run_daemon_que_recarga(bootstrap_fn, initial, **kwargs):
        vistos["al_arrancar"] = kwargs.get("al_arrancar")
        bootstrap_fn()
        bootstrap_fn()

    monkeypatch.setattr("inaki.app.runner.run_daemon", _run_daemon_que_recarga)

    bootstrap_mod.run_daemon_mode(Path("/cfg"), Path("/cfg/agents"), MagicMock(), MagicMock())

    (inicial, primer_reload, segundo_reload) = vistos["ensamblar"]
    assert inicial is not None
    assert primer_reload is inicial and segundo_reload is inicial
    assert vistos["bootstrap"] == [inicial, inicial]
    # El runner recibe el hook de avisos: sin él, el cambio aplicado quedaría mudo.
    assert callable(vistos["al_arrancar"])


def test_reload_con_overrides_cuyo_ensamblado_falla_sale_con_exit_1_sin_reintentar(
    sin_fs: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Sin rollback (D6): el ensamblado falla UNA vez y el proceso sale; no hay segundo
    intento "sin los overrides" en el mismo proceso (systemd reinicia desde disco)."""
    llamadas = {"n": 0}

    def _ensamblar(*_a, **_k):
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            return MagicMock(name="inicial")
        raise ConfigError("fuente de knowledge ilegible")

    monkeypatch.setattr("inaki.app.assembly.ensamblar", _ensamblar)
    monkeypatch.setattr(bootstrap_mod, "bootstrap", lambda *_a, **_k: (MagicMock(), MagicMock()))

    async def _run_daemon_que_recarga(bootstrap_fn, initial, **_k):
        bootstrap_fn()

    monkeypatch.setattr("inaki.app.runner.run_daemon", _run_daemon_que_recarga)

    with pytest.raises(SystemExit) as salida:
        bootstrap_mod.run_daemon_mode(Path("/cfg"), Path("/cfg/agents"), MagicMock(), MagicMock())

    assert salida.value.code == 1
    assert llamadas["n"] == 2
    assert "fuente de knowledge ilegible" in capsys.readouterr().err
