"""``ChannelRouter``: resolución por dueño, cascada de fallback y destinos del kernel."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from core.domain.services.channel_outbound_registry import ChannelOutboundRegistry
from core.domain.services.channel_router import (
    ChannelFallbackSettings,
    ChannelRouter,
    FileOutbound,
    NullOutbound,
    parse_target,
)
from core.domain.value_objects.outbound_kind import OutboundKind
from core.ports.outbound.channel_port import IChannelOutbound, OutboundIntermediateSink


class OutboundFalso(IChannelOutbound):
    """Outbound de prueba: registra cada ``send`` en un ``AsyncMock``."""

    def __init__(self, name: str = "telegram") -> None:
        self.channel_name = name
        self.send_mock = AsyncMock()

    def capabilities(self) -> set[OutboundKind]:
        return set(OutboundKind)

    async def send(self, **kwargs) -> None:  # type: ignore[override]
        await self.send_mock(**kwargs)


def _registro(*outbounds: IChannelOutbound) -> ChannelOutboundRegistry:
    reg = ChannelOutboundRegistry()
    for o in outbounds:
        reg.register(o)
    return reg


def _router(registros: dict[str, ChannelOutboundRegistry], **kwargs) -> ChannelRouter:
    return ChannelRouter(resolve_outbounds=lambda aid: registros.get(aid or ""), **kwargs)


# ---------------------------------------------------------------------------
# parse_target
# ---------------------------------------------------------------------------


def test_parse_target_separa_canal_y_chat_id() -> None:
    assert parse_target("telegram:-100123") == ("telegram", "-100123")
    assert parse_target("file:///var/log/x.log") == ("file", "/var/log/x.log")
    assert parse_target("null:") == ("null", "")


def test_parse_target_sin_prefijo_es_error() -> None:
    with pytest.raises(ValueError):
        parse_target("sin-prefijo")


# ---------------------------------------------------------------------------
# Resolución por dueño
# ---------------------------------------------------------------------------


async def test_envia_por_el_outbound_del_agente_dueno() -> None:
    tg_a, tg_b = OutboundFalso(), OutboundFalso()
    router = _router({"a": _registro(tg_a), "b": _registro(tg_b)})

    dr = await router.send_message("telegram:42", "hola", agent_id="a")

    tg_a.send_mock.assert_awaited_once_with(
        chat_id="42", kind=OutboundKind.TEXT, text="hola", record_history=True
    )
    tg_b.send_mock.assert_not_awaited()
    assert dr.original_target == dr.resolved_target == "telegram:42"


async def test_record_history_false_se_propaga_al_outbound() -> None:
    tg = OutboundFalso()
    router = _router({"a": _registro(tg)})

    await router.send_message("telegram:42", "hola", agent_id="a", record_history=False)

    assert tg.send_mock.await_args.kwargs["record_history"] is False  # type: ignore[union-attr]


async def test_sin_agente_nunca_persiste_aunque_resuelva() -> None:
    """Sin dueño el resolver puede devolver un registro (el del primer agente), pero
    el historial es de alguien: sin agente no se escribe en el de nadie."""
    tg = OutboundFalso()
    router = ChannelRouter(resolve_outbounds=lambda aid: _registro(tg))

    await router.send_message("telegram:42", "hola", agent_id=None)

    assert tg.send_mock.await_args.kwargs["record_history"] is False  # type: ignore[union-attr]


def test_is_conversational_depende_del_registro_del_agente() -> None:
    router = _router({"a": _registro(OutboundFalso())})

    assert router.is_conversational("telegram", "a") is True
    assert router.is_conversational("telegram", "b") is False
    assert router.is_conversational("cli", "a") is False


# ---------------------------------------------------------------------------
# Cascada de fallback
# ---------------------------------------------------------------------------


async def test_canal_sin_outbound_cae_al_override_del_canal(tmp_path: Path) -> None:
    destino = tmp_path / "cli.log"
    router = _router({}, fallback=ChannelFallbackSettings(overrides={"cli": f"file://{destino}"}))

    dr = await router.send_message("cli:local", "texto", agent_id="a")

    assert dr.original_target == "cli:local"
    assert dr.resolved_target == f"file://{destino}"
    assert "texto" in destino.read_text()


async def test_override_gana_al_default_y_el_default_al_hardcoded() -> None:
    cfg = ChannelFallbackSettings(default="null:default", overrides={"cli": "null:override"})
    router = _router({}, fallback=cfg, hardcoded_fallback="null:hardcoded")

    assert (await router.send_message("cli:x", "t")).resolved_target == "null:override"
    assert (await router.send_message("rest:x", "t")).resolved_target == "null:default"


async def test_sin_config_cae_al_hardcoded(tmp_path: Path) -> None:
    destino = tmp_path / "hardcoded.log"
    router = _router({}, hardcoded_fallback=f"file://{destino}")

    dr = await router.send_message("daemon:x", "linea")

    assert dr.resolved_target == f"file://{destino}"
    assert destino.read_text().endswith("| linea\n")


async def test_el_fallback_puede_ser_un_canal_del_agente() -> None:
    tg = OutboundFalso()
    router = _router(
        {"a": _registro(tg)}, fallback=ChannelFallbackSettings(overrides={"cli": "telegram:7"})
    )

    dr = await router.send_message("cli:local", "t", agent_id="a")

    assert dr.resolved_target == "telegram:7"
    tg.send_mock.assert_awaited_once()


async def test_nada_resuelve_es_error_explicito() -> None:
    router = _router({}, hardcoded_fallback="slack:nadie")

    with pytest.raises(ValueError, match="Ningún destino resuelve"):
        await router.send_message("cli:x", "t")


def test_build_intermediate_sink_usa_la_misma_cascada() -> None:
    tg = OutboundFalso()
    router = _router({"a": _registro(tg)})

    sink = router.build_intermediate_sink("telegram:9", agent_id="a")

    assert isinstance(sink, OutboundIntermediateSink)
    assert sink._outbound is tg and sink._chat_id == "9"


# ---------------------------------------------------------------------------
# Destinos del kernel
# ---------------------------------------------------------------------------


async def test_file_outbound_appendea_con_marca_de_tiempo(tmp_path: Path) -> None:
    destino = tmp_path / "sub" / "out.log"
    out = FileOutbound()

    await out.send(chat_id=str(destino), kind=OutboundKind.TEXT, text="uno")
    await out.send(chat_id=str(destino), kind=OutboundKind.TEXT, text="dos")

    lineas = destino.read_text().splitlines()
    assert [linea.split(" | ", 1)[1] for linea in lineas] == ["uno", "dos"]
    assert all("T" in linea.split(" | ")[0] for linea in lineas)


async def test_file_outbound_solo_texto(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        await FileOutbound().send(chat_id=str(tmp_path / "x"), kind=OutboundKind.PHOTO)


async def test_null_outbound_descarta_sin_fallar() -> None:
    await NullOutbound().send(chat_id="", kind=OutboundKind.TEXT, text="nada")
    assert NullOutbound().capabilities() == {OutboundKind.TEXT}
