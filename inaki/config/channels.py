"""Registro de canales — el CONTRATO que ``inaki.config`` conoce de un canal, sin conocer ninguno.

Un canal aporta: su **modelo** Pydantic (la sección ``channels.<nombre>``), sus
**migraciones** automáticas sobre los YAML del operador, un **aviso sobre el YAML
crudo** de cada agente (paths equivocados) y una **validación cruzada** entre
agentes (identidades de canal repetidas). Lo registra el composition root con los
canales INSTALADOS (``inaki.channels.registrar_canales_instalados``); con cien
canales publicados y dos instalados, el schema conoce dos.

Consumidores: ``AgentConfig._validar_channels`` (validación), ``loader`` (migraciones
y hooks), ``introspection`` (secretos) y ``docs`` (``config-reference.md``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

Migracion = Callable[[Path, Path], None]
"""``(config_dir, agents_dir) -> None``; idempotente; corre en ``ensure_user_config``."""
ValidacionRaw = Callable[[str, dict], None]
"""``(agent_id, yaml_crudo_del_agente) -> None``; avisa o lanza ``ConfigError``."""
ValidacionAgentes = Callable[[dict[str, Any]], None]
"""``({agent_id: AgentConfig}) -> None``; lanza ``ConfigError`` ante conflictos entre agentes."""


@dataclass(frozen=True)
class CanalRegistrado:
    nombre: str
    modelo: type[BaseModel]
    migraciones: tuple[Migracion, ...] = ()
    validar_raw: ValidacionRaw | None = None
    validar_agentes: ValidacionAgentes | None = None


_registro: dict[str, CanalRegistrado] = {}


def registrar_canal(
    nombre: str,
    modelo: type[BaseModel],
    *,
    migraciones: tuple[Migracion, ...] = (),
    validar_raw: ValidacionRaw | None = None,
    validar_agentes: ValidacionAgentes | None = None,
) -> None:
    """Registra (o reemplaza) el canal ``nombre``. Idempotente."""
    _registro[nombre] = CanalRegistrado(nombre, modelo, migraciones, validar_raw, validar_agentes)


def canal_registrado(nombre: str) -> CanalRegistrado | None:
    return _registro.get(nombre)


def canales_registrados() -> dict[str, CanalRegistrado]:
    """Copia ordenada por nombre del registro actual."""
    return {k: _registro[k] for k in sorted(_registro)}


def limpiar_registro() -> None:
    """Vacía el registro. Solo para tests que prueban el registro en sí."""
    _registro.clear()
