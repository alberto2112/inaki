"""Guard del borde de config: una config rota se LEE, no se descifra.

``config-falla-ruidoso`` prometió que una config que no carga nombra el fichero,
el bloque y la clave a corregir. El dominio cumplía; el borde no. ``_bootstrap``
envolvía en ``try`` la carga del global y **nada más**, así que todo lo que valida
el YAML de un agente salía como traceback de Python con el mensaje bueno enterrado
treinta frames más abajo — y ``inaki config show``, la única herramienta de
diagnóstico del operador, ni siquiera validaba: devolvía **exit 0** listando como
campo válido justo aquello por lo que el daemon se negaba a arrancar.

Estos tests fijan las dos mitades de la regla:

1. Lo que es config del operador sale como UNA línea legible y exit 1.
2. Lo que es un bug NUESTRO sigue saliendo como traceback — ahí el traceback es
   la información, y disfrazarlo de "revisá tu YAML" manda a buscar un typo que
   no existe.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import pytest
import yaml
from pydantic import BaseModel
from ruamel.yaml import YAMLError as RuamelYAMLError
from typer.testing import CliRunner

from core.domain.errors import ConfigError
from inaki.config_errors import borde_de_config
from infrastructure.home import set_inaki_home


@pytest.fixture
def home(tmp_path: Path) -> Iterator[Path]:
    """Aísla el home de la instancia: sin esto los tests leerían el ``~/.inaki`` real."""
    set_inaki_home(tmp_path)
    yield tmp_path
    set_inaki_home(None)


def _salida(result: Any) -> str:
    """Todo lo que el comando escribió. Click separa stderr según versión."""
    partes = [result.output or ""]
    try:
        partes.append(result.stderr or "")
    except (ValueError, AttributeError):
        pass
    return "".join(partes)


# ---------------------------------------------------------------------------
# El borde atrapa lo que es config del operador
# ---------------------------------------------------------------------------


def _modelo_estricto() -> type[BaseModel]:
    class Estricto(BaseModel, extra="forbid"):
        campo: int

    return Estricto


@pytest.mark.parametrize(
    "excepcion",
    [
        ConfigError("agents/dev.yaml: 'schedulr' no existe"),
        yaml.YAMLError("indentación rota"),
        RuamelYAMLError("clave duplicada"),
        PermissionError(13, "Permission denied"),
    ],
    ids=["config_error", "yaml_pyyaml", "yaml_ruamel", "permisos"],
)
def test_lo_que_es_config_del_operador_sale_limpio(
    excepcion: Exception, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        with borde_de_config():
            raise excepcion

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert err.startswith("Error de configuración")
    assert "Traceback" not in err


def test_un_valor_que_el_schema_rechaza_tambien_sale_limpio(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``ValidationError`` no es ``ConfigError``, pero es igual de culpa del YAML."""
    with pytest.raises(SystemExit):
        with borde_de_config():
            _modelo_estricto()(campo="no-es-un-int")  # type: ignore[arg-type]

    assert "Error de configuración" in capsys.readouterr().err


def test_un_bug_nuestro_sigue_saliendo_como_traceback() -> None:
    """El borde NO es un tragador de excepciones.

    Un `AttributeError` nuestro disfrazado de "revisá tu config" manda al
    operador a buscar un typo que no existe.
    """
    with pytest.raises(AttributeError):
        with borde_de_config("/home/pi/.inaki"):
            raise AttributeError("None no tiene 'execute'")


# ---------------------------------------------------------------------------
# El contexto se agrega solo cuando suma
# ---------------------------------------------------------------------------


def test_no_repite_el_path_que_el_error_ya_trae(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        with borde_de_config("/home/pi/.inaki/agents"):
            raise ConfigError("/home/pi/.inaki/agents/dev.yaml: 'schedulr' no existe")

    err = capsys.readouterr().err
    assert err.count("/home/pi/.inaki/agents") == 1, "el path repetido tapa lo que hay que leer"


def test_agrega_el_contexto_cuando_el_error_no_nombra_fichero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Un shape legacy no dice de qué instancia habla, y con ``--home`` hay varias."""
    with pytest.raises(SystemExit):
        with borde_de_config("/srv/inaki-deptB/config"):
            raise ConfigError("Formato legacy detectado en config: 'llm.api_key' ya no existe")

    assert "/srv/inaki-deptB/config" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Los dos comandos que el operador tipea cuando algo no anda
# ---------------------------------------------------------------------------

_AGENTE_ROTO = "id: dev\nname: Dev\ndescription: d\nschedulr:\n  enabled: true\n"


def test_el_arranque_no_tira_traceback_con_un_agente_roto(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """El agujero original: ``AgentRegistry`` quedaba fuera del ``try``."""
    from infrastructure.config import ensure_user_config

    from inaki.cli import _bootstrap

    config_dir, agents_dir = home / "config", home / "agents"
    ensure_user_config(config_dir, agents_dir)
    (agents_dir / "dev.yaml").write_text(_AGENTE_ROTO, encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        _bootstrap(config_dir, agents_dir)

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "dev.yaml" in err
    assert "schedulr" in err
    assert "Traceback" not in err


def test_config_show_no_dice_ok_con_una_config_que_no_arranca(home: Path) -> None:
    """La regresión que más daño hizo.

    ``show`` mergea los YAML crudos contra los defaults del schema sin validar:
    con ``schedulr:`` en un agente devolvía exit 0 listándolo como campo real,
    mientras el daemon se negaba a levantar por esa misma clave. El operador
    leía el OK de su única herramienta de diagnóstico.
    """
    from inaki.cli import app

    agents_dir = home / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / "dev.yaml").write_text(_AGENTE_ROTO, encoding="utf-8")

    result = CliRunner().invoke(app, ["config", "show"])

    assert result.exit_code == 1, "un exit 0 acá es la herramienta mintiendo"
    salida = _salida(result)
    assert "schedulr" in salida
    assert "schedulr.enabled" not in salida, "no listarlo como si fuera config real"


def test_config_show_sigue_andando_con_una_config_sana(home: Path) -> None:
    """La validación no puede volverse un portón cerrado: el camino feliz manda."""
    from inaki.cli import app

    agents_dir = home / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / "dev.yaml").write_text("id: dev\nname: Dev\ndescription: d\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["config", "show", "--agent", "dev"])

    assert result.exit_code == 0, _salida(result)
    assert "llm.provider" in _salida(result)
