"""Tools del LLM: registro con routing semántico, builtins y Tool Config Protocol.

Este paquete es también la FACHADA PÚBLICA para escribir tools de terceros
(extensiones): ``from inaki.tools import ITool, ToolResult, IToolConfigStore``.
Los contratos viven en el kernel (los consume el turno), pero una extensión no
tiene por qué saber dónde: importa de acá y la próxima mudanza interna no la toca.
"""

from inaki.kernel.ports.tool_config_port import IToolConfigStore
from inaki.kernel.ports.tool_port import ITool, IToolExecutor, ToolResult

__all__ = ["ITool", "IToolConfigStore", "IToolExecutor", "ToolResult"]
