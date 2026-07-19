"""Tests del schema de topología de ``BroadcastConfig`` (rol explícito).

Cubre el contrato del rediseño server/client:
  - server XOR client (ambos o ninguno → error),
  - auth único obligatorio con enabled=True,
  - enabled=False relaja topología y auth (kill-switch sin borrar el bloque),
  - rangos de puerto validados por los sub-modelos.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from infrastructure.config import (
    BroadcastClientConfig,
    BroadcastConfig,
    BroadcastServerConfig,
)

# ---------------------------------------------------------------------------
# Roles válidos
# ---------------------------------------------------------------------------


def test_modo_server_valido():
    cfg = BroadcastConfig(auth="s" * 16, server=BroadcastServerConfig(port=6499))
    assert cfg.enabled is True
    assert cfg.server is not None and cfg.server.port == 6499
    assert cfg.client is None


def test_modo_client_valido():
    cfg = BroadcastConfig(
        auth="s" * 16, client=BroadcastClientConfig(host="192.168.1.50", port=6499)
    )
    assert cfg.client is not None
    assert cfg.client.host == "192.168.1.50"
    assert cfg.client.port == 6499
    assert cfg.server is None


# ---------------------------------------------------------------------------
# Topología inválida
# ---------------------------------------------------------------------------


def test_server_y_client_simultaneos_rechazado():
    with pytest.raises(ValidationError, match="mutuamente excluyentes"):
        BroadcastConfig(
            auth="s",
            server=BroadcastServerConfig(port=6499),
            client=BroadcastClientConfig(host="h", port=6500),
        )


def test_sin_server_ni_client_rechazado():
    with pytest.raises(ValidationError, match="'server'.*'client'"):
        BroadcastConfig(auth="s")


def test_auth_obligatorio_con_enabled():
    with pytest.raises(ValidationError, match="auth"):
        BroadcastConfig(server=BroadcastServerConfig(port=6499))


# ---------------------------------------------------------------------------
# Kill-switch enabled=False
# ---------------------------------------------------------------------------


def test_enabled_false_permite_bloque_incompleto():
    """``broadcast: {enabled: false}`` es un kill-switch válido: no se exige
    topología ni auth mientras el transporte está apagado."""
    cfg = BroadcastConfig(enabled=False)
    assert cfg.enabled is False
    assert cfg.server is None and cfg.client is None and cfg.auth is None


def test_enabled_false_conserva_config_completa():
    """Apagar no borra: el bloque completo sigue cargando con enabled=false."""
    cfg = BroadcastConfig(enabled=False, auth="s", server=BroadcastServerConfig(port=6499))
    assert cfg.server is not None and cfg.server.port == 6499


# ---------------------------------------------------------------------------
# Rangos de puerto (validados por los sub-modelos, no por el validador padre)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("puerto", [80, 1023, 65536])
def test_server_port_fuera_de_rango(puerto: int):
    with pytest.raises(ValidationError):
        BroadcastServerConfig(port=puerto)


@pytest.mark.parametrize("puerto", [80, 1023, 65536])
def test_client_port_fuera_de_rango(puerto: int):
    with pytest.raises(ValidationError):
        BroadcastClientConfig(host="h", port=puerto)
