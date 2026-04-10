"""ShellTool — ejecución de comandos shell."""

from __future__ import annotations

import asyncio
import logging

from core.ports.outbound.tool_port import ITool, ToolResult

logger = logging.getLogger(__name__)

_TIMEOUT = 30  # segundos


class ShellTool(ITool):
    name = "run_shell"
    description = (
        "Ejecuta un comando shell en el sistema. "
        "NUNCA usar flags destructivos sin confirmación explícita del usuario. "
        "Reportar el output verbatim sin interpretarlo."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "El comando shell a ejecutar",
            },
            "working_dir": {
                "type": "string",
                "description": "Directorio de trabajo (opcional)",
            },
        },
        "required": ["command"],
    }

    async def execute(self, command: str, working_dir: str | None = None, **kwargs) -> ToolResult:
        logger.info("ShellTool ejecutando: %s", command)
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=working_dir,
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_TIMEOUT)
            except asyncio.TimeoutError:
                proc.kill()
                return ToolResult(
                    tool_name=self.name,
                    output=f"Timeout ({_TIMEOUT}s) ejecutando: {command}",
                    success=False,
                    error="timeout",
                )

            output = stdout.decode("utf-8", errors="replace").strip()
            success = proc.returncode == 0

            if not success:
                logger.warning("ShellTool: exit code %d para: %s", proc.returncode, command)

            return ToolResult(
                tool_name=self.name,
                output=output or "(sin output)",
                success=success,
                error=None if success else f"exit code {proc.returncode}",
            )
        except Exception as exc:
            return ToolResult(
                tool_name=self.name,
                output=f"Error: {exc}",
                success=False,
                error=str(exc),
            )
