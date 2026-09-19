"""``inaki.app.settings``: config → VOs del kernel; la timezone del usuario llega al turno."""

from __future__ import annotations

from inaki.app.settings import build_run_agent_settings
from inaki.config import CaptureConfig, MemoriesConfig, ToolsConfig
from tests.app.conftest import agent_cfg


def test_la_timezone_del_usuario_viaja_al_prompt_por_settings() -> None:
    """Regresión: ``user.timezone`` se ignoraba en el prompt (el setter que la
    inyectaba no lo llamaba nadie) y el kernel caía a la TZ del sistema."""
    settings = build_run_agent_settings(agent_cfg("dev"), user_timezone="Europe/Madrid")
    assert settings.user_timezone == "Europe/Madrid"


def test_sin_timezone_explicita_el_vo_no_inventa_una() -> None:
    assert build_run_agent_settings(agent_cfg("dev")).user_timezone is None


# ---------------------------------------------------------------------------
# Pin condicional de `memory` — fase 4 del plan `memory-tool-unificada`
# ---------------------------------------------------------------------------


def test_memory_pinneada_por_default_capture_enabled() -> None:
    """``memories.capture.enabled`` es ``true`` por default: `memory` se pinnea
    sin que el usuario tenga que tocar `tools.pinned`."""
    settings = build_run_agent_settings(agent_cfg("dev"))
    assert "memory" in settings.tools_pinned


def test_memory_no_pinneada_con_capture_deshabilitado() -> None:
    """Apagar la captura en vivo saca `memory` del pin — pero la tool sigue
    registrada (search/list/update/delete no dependen de `capture`)."""
    cfg = agent_cfg("dev").model_copy(
        update={
            "memories": MemoriesConfig(db_filename=":memory:", capture=CaptureConfig(enabled=False))
        }
    )
    settings = build_run_agent_settings(cfg)
    assert "memory" not in settings.tools_pinned


def test_tools_pinned_explicito_del_usuario_se_respeta_y_suma_memory() -> None:
    """Un `tools.pinned` explícito no pierde sus entradas: `memory` se UNE, no
    reemplaza — mismo criterio que sostiene a `delegate` en el default."""
    cfg = agent_cfg("dev").model_copy(update={"tools": ToolsConfig(pinned=["delegate", "custom"])})
    settings = build_run_agent_settings(cfg)
    assert settings.tools_pinned == frozenset({"delegate", "custom", "memory"})
