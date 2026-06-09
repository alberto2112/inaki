"""Tests para ChannelOutboundRegistry."""

from __future__ import annotations

import pytest

from adapters.outbound.messaging.channel_outbound_registry import ChannelOutboundRegistry
from core.domain.value_objects.outbound_kind import OutboundKind
from core.ports.outbound.channel_outbound_port import IChannelOutbound


# ---------------------------------------------------------------------------
# Fake adapter para tests
# ---------------------------------------------------------------------------


class _FakeAdapter(IChannelOutbound):
    def __init__(self, nombre: str, kinds: set[OutboundKind]) -> None:
        self.channel_name = nombre
        self._kinds = kinds

    def capabilities(self) -> set[OutboundKind]:
        return self._kinds

    async def send(self, *, chat_id, kind, text=None, sources=None, caption=None) -> None:
        pass  # pragma: no cover


def _text_adapter(nombre: str = "canal-a") -> _FakeAdapter:
    return _FakeAdapter(nombre, {OutboundKind.TEXT})


def _full_adapter(nombre: str = "canal-b") -> _FakeAdapter:
    return _FakeAdapter(nombre, {OutboundKind.TEXT, OutboundKind.PHOTO, OutboundKind.ALBUM})


# ---------------------------------------------------------------------------
# register + get
# ---------------------------------------------------------------------------


def test_register_y_get_devuelve_el_adapter():
    registry = ChannelOutboundRegistry()
    adapter = _text_adapter("telegram")
    registry.register(adapter)

    resultado = registry.get("telegram")

    assert resultado is adapter


def test_get_canal_desconocido_lanza_key_error():
    registry = ChannelOutboundRegistry()
    registry.register(_text_adapter("telegram"))

    with pytest.raises(KeyError, match="'slack'"):
        registry.get("slack")


def test_get_mensaje_de_error_lista_canales_disponibles():
    registry = ChannelOutboundRegistry()
    registry.register(_text_adapter("telegram"))
    registry.register(_full_adapter("discord"))

    with pytest.raises(KeyError) as exc_info:
        registry.get("slack")

    # El mensaje debe mencionar los canales registrados
    mensaje = str(exc_info.value)
    assert "discord" in mensaje
    assert "telegram" in mensaje


def test_get_sin_adapters_registrados_lanza_key_error():
    registry = ChannelOutboundRegistry()

    with pytest.raises(KeyError, match="ninguno"):
        registry.get("cualquier-cosa")


# ---------------------------------------------------------------------------
# supports
# ---------------------------------------------------------------------------


def test_supports_devuelve_true_cuando_canal_y_kind_coinciden():
    registry = ChannelOutboundRegistry()
    registry.register(_full_adapter("telegram"))

    assert registry.supports("telegram", OutboundKind.PHOTO) is True


def test_supports_devuelve_false_cuando_kind_no_soportado():
    registry = ChannelOutboundRegistry()
    registry.register(_text_adapter("telegram"))  # solo TEXT

    assert registry.supports("telegram", OutboundKind.PHOTO) is False


def test_supports_devuelve_false_cuando_canal_no_registrado():
    registry = ChannelOutboundRegistry()

    assert registry.supports("inexistente", OutboundKind.TEXT) is False


# ---------------------------------------------------------------------------
# list_channels
# ---------------------------------------------------------------------------


def test_list_channels_devuelve_lista_vacia_inicial():
    registry = ChannelOutboundRegistry()

    assert registry.list_channels() == []


def test_list_channels_devuelve_canales_en_orden_insercion():
    registry = ChannelOutboundRegistry()
    registry.register(_text_adapter("telegram"))
    registry.register(_full_adapter("discord"))

    canales = registry.list_channels()

    assert canales == ["telegram", "discord"]


# ---------------------------------------------------------------------------
# Sobreescritura con warning
# ---------------------------------------------------------------------------


def test_register_sobreescribe_adapter_existente(caplog):
    import logging

    registry = ChannelOutboundRegistry()
    adapter1 = _text_adapter("telegram")
    adapter2 = _full_adapter("telegram")

    with caplog.at_level(logging.WARNING):
        registry.register(adapter1)
        registry.register(adapter2)

    assert registry.get("telegram") is adapter2
    assert any("sobreescribiendo" in r.message for r in caplog.records)
