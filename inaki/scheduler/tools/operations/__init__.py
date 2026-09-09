"""Tabla de despacho de la tool ``scheduler``: nombre de operación → clase.

Agregar una operación es agregar una entrada acá y un objeto en su módulo; la
fachada no cambia. El orden es el que ve el LLM en los mensajes de error.
"""

from inaki.scheduler.tools.operations._base import Operacion
from inaki.scheduler.tools.operations.actualizar import Actualizar
from inaki.scheduler.tools.operations.consultar import Listar, Obtener
from inaki.scheduler.tools.operations.correr import Correr
from inaki.scheduler.tools.operations.crear import Crear
from inaki.scheduler.tools.operations.estado import Borrar, Deshabilitar, Habilitar
from inaki.scheduler.tools.operations.logs import ListarLogs, ObtenerLog

OPERACIONES: dict[str, type[Operacion]] = {
    "create": Crear,
    "list": Listar,
    "get": Obtener,
    "update": Actualizar,
    "delete": Borrar,
    "enable": Habilitar,
    "disable": Deshabilitar,
    "run": Correr,
    "logs": ListarLogs,
    "log_get": ObtenerLog,
}

__all__ = ["OPERACIONES", "Operacion"]
