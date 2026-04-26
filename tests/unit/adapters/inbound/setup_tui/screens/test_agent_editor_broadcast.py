"""
Tests del escenario de modal de broadcast ambiguo (UX-decision#3).

Prueba las funciones puras de detección y resolución sin montar el widget.

Escenarios:
  1. YAML con ambos modos → detectar_broadcast_ambiguo retorna True
  2. Elegir Server → resolver_broadcast_server elimina remote.host
  3. Elegir Client → resolver_broadcast_client elimina port
  4. Cancelar → no hay modificación (lógica en la pantalla, testeada vía estado)
  5. YAML con solo server → no ambiguo
  6. YAML con solo client → no ambiguo
  7. YAML sin broadcast → no ambiguo
"""

from __future__ import annotations

import copy


from adapters.inbound.setup_tui.screens.agent_editor_screen import (
    detectar_broadcast_ambiguo,
    resolver_broadcast_client,
    resolver_broadcast_server,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _yaml_ambiguo() -> dict:
    """Config con ambos modos broadcast definidos (estado inválido)."""
    return {
        "id": "test-agent",
        "name": "Test",
        "channels": {
            "telegram": {
                "broadcast": {
                    "port": 9876,
                    "remote": {
                        "host": "192.168.1.100",
                        "port": 9876,
                    },
                }
            }
        },
    }


def _yaml_solo_server() -> dict:
    return {
        "channels": {
            "telegram": {
                "broadcast": {
                    "port": 9876,
                }
            }
        }
    }


def _yaml_solo_client() -> dict:
    return {
        "channels": {
            "telegram": {
                "broadcast": {
                    "remote": {
                        "host": "192.168.1.100",
                        "port": 9876,
                    }
                }
            }
        }
    }


# ---------------------------------------------------------------------------
# Tests de detección
# ---------------------------------------------------------------------------


class TestDetectarBroadcastAmbiguo:
    def test_ambos_modos_es_ambiguo(self) -> None:
        assert detectar_broadcast_ambiguo(_yaml_ambiguo()) is True

    def test_solo_server_no_es_ambiguo(self) -> None:
        assert detectar_broadcast_ambiguo(_yaml_solo_server()) is False

    def test_solo_client_no_es_ambiguo(self) -> None:
        assert detectar_broadcast_ambiguo(_yaml_solo_client()) is False

    def test_sin_broadcast_no_es_ambiguo(self) -> None:
        datos = {"id": "test", "name": "Test"}
        assert detectar_broadcast_ambiguo(datos) is False

    def test_sin_channels_no_es_ambiguo(self) -> None:
        assert detectar_broadcast_ambiguo({}) is False

    def test_broadcast_vacio_no_es_ambiguo(self) -> None:
        datos = {"channels": {"telegram": {"broadcast": {}}}}
        assert detectar_broadcast_ambiguo(datos) is False

    def test_solo_port_sin_remote_no_es_ambiguo(self) -> None:
        datos = {"channels": {"telegram": {"broadcast": {"port": 1234}}}}
        assert detectar_broadcast_ambiguo(datos) is False

    def test_remote_sin_host_no_cuenta_como_client(self) -> None:
        # remote existe pero sin el campo "host" → no es modo client válido
        datos = {
            "channels": {
                "telegram": {
                    "broadcast": {
                        "port": 9876,
                        "remote": {"port": 9876},  # falta "host"
                    }
                }
            }
        }
        assert detectar_broadcast_ambiguo(datos) is False


# ---------------------------------------------------------------------------
# Tests de resolución — elegir Server
# ---------------------------------------------------------------------------


class TestResolverBroadcastServer:
    def test_elegir_server_elimina_remote(self) -> None:
        datos = _yaml_ambiguo()
        resultado = resolver_broadcast_server(datos)
        broadcast = resultado["channels"]["telegram"]["broadcast"]
        assert "remote" not in broadcast

    def test_elegir_server_mantiene_port(self) -> None:
        datos = _yaml_ambiguo()
        resultado = resolver_broadcast_server(datos)
        broadcast = resultado["channels"]["telegram"]["broadcast"]
        assert broadcast["port"] == 9876

    def test_resolver_server_no_muta_original(self) -> None:
        datos = _yaml_ambiguo()
        datos_copia = copy.deepcopy(datos)
        resolver_broadcast_server(datos)
        # El original no debe haber cambiado
        assert datos == datos_copia

    def test_resolver_server_sin_broadcast_es_noop(self) -> None:
        datos = {"id": "test"}
        resultado = resolver_broadcast_server(datos)
        assert resultado == datos


# ---------------------------------------------------------------------------
# Tests de resolución — elegir Client
# ---------------------------------------------------------------------------


class TestResolverBroadcastClient:
    def test_elegir_client_elimina_port(self) -> None:
        datos = _yaml_ambiguo()
        resultado = resolver_broadcast_client(datos)
        broadcast = resultado["channels"]["telegram"]["broadcast"]
        assert "port" not in broadcast

    def test_elegir_client_mantiene_remote_host(self) -> None:
        datos = _yaml_ambiguo()
        resultado = resolver_broadcast_client(datos)
        broadcast = resultado["channels"]["telegram"]["broadcast"]
        assert broadcast["remote"]["host"] == "192.168.1.100"

    def test_resolver_client_no_muta_original(self) -> None:
        datos = _yaml_ambiguo()
        datos_copia = copy.deepcopy(datos)
        resolver_broadcast_client(datos)
        assert datos == datos_copia

    def test_resolver_client_sin_broadcast_es_noop(self) -> None:
        datos = {"id": "test"}
        resultado = resolver_broadcast_client(datos)
        assert resultado == datos


# ---------------------------------------------------------------------------
# Test de invariante: resolver_server + resolver_client son excluyentes
# ---------------------------------------------------------------------------


class TestResolverEsExcluyente:
    def test_server_y_client_producen_configs_disjuntas(self) -> None:
        datos = _yaml_ambiguo()
        server = resolver_broadcast_server(datos)
        client = resolver_broadcast_client(datos)

        broadcast_server = server["channels"]["telegram"]["broadcast"]
        broadcast_client = client["channels"]["telegram"]["broadcast"]

        assert "port" in broadcast_server
        assert "remote" not in broadcast_server

        assert "port" not in broadcast_client
        assert "remote" in broadcast_client
