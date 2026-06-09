"""get_world_state — Genera un resumen del estado actual del mundo consultando scheduler, calendario y memoria"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from core.ports.outbound.tool_port import ITool, ToolResult

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT = Path.home() / ".inaki" / "users" / "telegram" / "WORLD_STATE.md"


class GetWorldStateTool(ITool):
    name = "get_world_state"
    description = "Genera un resumen del estado actual del mundo consultando scheduler, calendario y memoria"
    parameters_schema = {
        "type": "object",
        "properties": {
            "output_file": {
                "type": "string",
                "description": "Ruta del archivo donde escribir el estado (default: ~/.inaki/users/telegram/WORLD_STATE.md)"
            }
        },
        "required": []
    }

    async def execute(self, **kwargs) -> ToolResult:
        output_path = Path(kwargs.get("output_file", str(DEFAULT_OUTPUT)))
        output_path.parent.mkdir(parents=True, exist_ok=True)

        now = datetime.now()
        timestamp = now.strftime("%Y-%m-%d %H:%M")

        # Este tool es un helper: genera la estructura del archivo.
        # El contenido real lo rellena el agente usando sus otras tools
        # (scheduler.list, exchange_calendar.search, search_memory).
        # Aquí solo creamos el esqueleto para que el agente lo complete.

        content = f"""# Estado del mundo — {timestamp}

## Tareas programadas activas
<!-- El agente debe rellenar esta sección usando scheduler.list() -->

## Próximos eventos (7 días)
<!-- El agente debe rellenar esta sección usando exchange_calendar.search() -->

## Proyectos activos (de memoria)
<!-- El agente debe rellenar esta sección usando search_memory() -->

## Alertas pendientes
- (ninguna)

## Hilos abiertos
- (ninguno detectado)

---
*Generado automáticamente — última actualización: {timestamp}*
"""
        output_path.write_text(content, encoding="utf-8")

        return ToolResult(
            success=True,
            data={
                "output_file": str(output_path),
                "timestamp": timestamp,
                "message": f"Esqueleto generado en {output_path}. El agente debe rellenar las secciones usando scheduler.list(), exchange_calendar.search() y search_memory()."
            }
        )
