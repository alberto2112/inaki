"""Bloque ``admin``: servidor REST del daemon.

Sección del schema de configuración. Solo declaraciones: sin I/O ni carga de YAML.
Importá desde ``inaki.config.schema`` (o ``inaki.config``).
"""

from __future__ import annotations

from pydantic import Field

from inaki.config.schema._base import _ConfigBaseModel


class AdminConfig(_ConfigBaseModel):
    """Configuración del admin server del daemon."""

    port: int = 6497
    """Puerto TCP en el que escucha el admin server del daemon.

    Es también el puerto al que apunta la CLI para hablar con el daemon local
    (``inaki chat``, ``inaki tool``, ``inaki scheduler``)."""

    host: str = "127.0.0.1"
    """Interfaz en la que bindea el admin server.

    El default ``127.0.0.1`` lo deja accesible SOLO desde la propia máquina.
    Ponerlo en ``0.0.0.0`` lo expone a la LAN: hacelo únicamente con ``auth_key``
    configurada."""

    auth_key: str | None = Field(default=None, json_schema_extra={"secret": True})
    """Credencial del header ``X-Admin-Key`` que protege los endpoints de gestión.

    Es un SECRETO: ``inaki config show`` lo redacta. Con ``null`` (default) el daemon arranca
    igual pero loggea un WARNING y los endpoints protegidos responden 403 — o
    sea, sin clave no se administra. La CLI la toma de acá salvo que se le pase
    ``--remote-key``."""

    chat_timeout: float = 300.0
    """Timeout en segundos para turnos de chat vía REST (POST /admin/chat/turn)."""
