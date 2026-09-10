"""Canales de Inaki — un canal = un paquete bajo ``inaki/channels/``.

Cada canal implementa el contrato del kernel (``inaki/kernel/ports/outbound/channel_port.py``):
ciclo de vida ``IChannel``, egress ``IChannelOutbound``, y aporta su sección de config
al registro de ``inaki.config.channels``. El composition root llama a
``registrar_canales_instalados()`` ANTES de cargar config: con cien canales
publicados y dos instalados, el schema conoce dos.

Hoy la lista es explícita; para distribución pasa a descubrimiento por
``entry_points`` (grupo ``inaki.channels``), el mecanismo estándar de plugins pip.
"""

from __future__ import annotations


def registrar_canales_instalados() -> None:
    """Registra en ``inaki.config`` la sección de cada canal instalado. Idempotente."""
    from inaki.channels.cli import config as cli_config
    from inaki.channels.telegram import config as telegram_config

    telegram_config.registrar()
    cli_config.registrar()
