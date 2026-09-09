"""``inaki.app.settings``: config → VOs del kernel; la timezone del usuario llega al turno."""

from __future__ import annotations

from inaki.app.settings import build_run_agent_settings
from tests.app.conftest import agent_cfg


def test_la_timezone_del_usuario_viaja_al_prompt_por_settings() -> None:
    """Regresión: ``user.timezone`` se ignoraba en el prompt (el setter que la
    inyectaba no lo llamaba nadie) y el kernel caía a la TZ del sistema."""
    settings = build_run_agent_settings(agent_cfg("dev"), user_timezone="Europe/Madrid")
    assert settings.user_timezone == "Europe/Madrid"


def test_sin_timezone_explicita_el_vo_no_inventa_una() -> None:
    assert build_run_agent_settings(agent_cfg("dev")).user_timezone is None
