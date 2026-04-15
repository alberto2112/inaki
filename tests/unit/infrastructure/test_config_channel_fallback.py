"""Tests para ChannelFallbackConfig y su integración con SchedulerConfig."""

from __future__ import annotations

from infrastructure.config import ChannelFallbackConfig, SchedulerConfig


def test_channel_fallback_default_sin_entradas() -> None:
    cfg = ChannelFallbackConfig()
    assert cfg.default is None
    assert cfg.overrides == {}


def test_channel_fallback_con_default_string() -> None:
    cfg = ChannelFallbackConfig(default="file:///tmp/out.log")
    assert cfg.default == "file:///tmp/out.log"
    assert cfg.overrides == {}


def test_channel_fallback_con_overrides() -> None:
    cfg = ChannelFallbackConfig(overrides={"cli": "telegram:123", "rest": "null:"})
    assert cfg.default is None
    assert cfg.overrides == {"cli": "telegram:123", "rest": "null:"}


def test_channel_fallback_default_y_overrides_juntos() -> None:
    cfg = ChannelFallbackConfig(
        default="null:",
        overrides={"cli": "telegram:999"},
    )
    assert cfg.default == "null:"
    assert cfg.overrides == {"cli": "telegram:999"}


def test_scheduler_config_expone_channel_fallback_default() -> None:
    scfg = SchedulerConfig()
    assert isinstance(scfg.channel_fallback, ChannelFallbackConfig)
    assert scfg.channel_fallback.default is None
    assert scfg.channel_fallback.overrides == {}


def test_scheduler_config_acepta_channel_fallback_custom() -> None:
    scfg = SchedulerConfig(
        channel_fallback=ChannelFallbackConfig(
            default="file:///var/log/x.log",
            overrides={"daemon": "null:"},
        )
    )
    assert scfg.channel_fallback.default == "file:///var/log/x.log"
    assert scfg.channel_fallback.overrides == {"daemon": "null:"}


def test_scheduler_config_acepta_dict_anidado_yaml_like() -> None:
    """Simula cómo pydantic recibe el dict tras el merge de YAMLs."""
    scfg = SchedulerConfig(
        **{
            "channel_fallback": {
                "default": "file:///tmp/x.log",
                "overrides": {"cli": "telegram:42"},
            }
        }
    )
    assert scfg.channel_fallback.default == "file:///tmp/x.log"
    assert scfg.channel_fallback.overrides == {"cli": "telegram:42"}
