"""``user.timezone`` se resuelve también cuando el bloque ``user:`` no está en el YAML.

Bug cazado por el camino dorado ``tests/integration/golden`` (fase 0 del refactor
modular): el validador ``_resolve_timezone`` corría sobre ``timezone: ""`` explícito
pero NO sobre el default de la clase, así que un ``global.yaml`` sin ``user:``
entregaba ``""`` al scheduler y el container moría con un ``ValueError`` de
``ZoneInfo`` sin ninguna pista de config.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

from infrastructure.config_schema import UserConfig


def test_default_sin_bloque_user_resuelve_una_zona_valida() -> None:
    cfg = UserConfig()

    assert cfg.timezone != ""
    ZoneInfo(cfg.timezone)  # no lanza: es una zona IANA real


def test_default_y_vacio_explicito_resuelven_igual() -> None:
    assert UserConfig().timezone == UserConfig(timezone="").timezone
