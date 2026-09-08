"""Canales de Inaki — un canal = un paquete bajo ``inaki/channels/``.

Cada canal implementa el contrato del kernel (``core/ports/outbound/channel_port.py``):
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
    from inaki.channels.telegram import config as telegram_config
    from inaki.config.channels import registrar_canal
    from inaki.config.schema.channels import CliChannelConfig

    telegram_config.registrar()
    # El canal CLI (chat interactivo vía REST) se muda a su paquete en la fase 5;
    # su sección sigue en el schema base hasta entonces.
    registrar_canal("cli", CliChannelConfig)
