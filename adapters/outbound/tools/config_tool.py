"""ConfigTool — le da al agente acceso de lectura a su propia configuración.

Responde "¿con qué parámetros estoy corriendo?" sobre el snapshot que el
proceso tiene CARGADO, no sobre los YAML. La diferencia importa: entre lo
escrito y lo cargado hay un reinicio de por medio, y además el schema coerce
valores al validar. Un agente que leyera los ficheros contestaría con
seguridad algo que no es lo que está ejecutando.

Es de SOLO LECTURA por diseño. Escribir un parámetro en caliente no existe como
concepto en este sistema: los providers y los use cases reciben sus valores al
construirse (``ResolvedLLMConfig``, los Settings VO ``frozen=True``), así que
mutar el objeto de config no cambiaría el comportamiento de la siguiente
llamada — solo haría que la tool y el runtime discrepen. Cambiar config de
verdad es escribir la capa y recargar el daemon (``inaki reload``).

Las credenciales NO salen de acá con ningún parámetro: el snapshot que recibe
esta tool ya viene redactado desde ``RuntimeConfigUseCase``. De un secreto se
puede saber si está puesto o si falta, nunca su valor.
"""

from __future__ import annotations

from core.ports.outbound.tool_port import ITool, ToolResult
from core.use_cases.config.runtime_config import CampoRuntime, RuntimeConfigUseCase

_MAX_CAMPOS = 120
"""Techo de campos por ``list``. La config entera son ~90 campos y las
extensiones la agrandan; volcarla completa en cada consulta se come el
presupuesto de tokens del turno para responder una pregunta puntual."""


class ConfigTool(ITool):
    name = "config"
    description = (
        "Read the agent's own live configuration: the parameters currently loaded "
        "in memory, not what is written in the config files. "
        "Required parameter: 'operation', one of: "
        "'get' (read one parameter; needs 'path'), "
        "'list' (show the parameters under a section; optional 'prefix'). "
        "Paths are dotted, e.g. 'llm.model', 'llm.temperature', 'memories.db_filename', "
        "'tools.semantic_routing_top_k'. There is no 'global.' or 'agent.' prefix: "
        "those are the config LAYER a value comes from, which is reported as its origin. "
        "Use 'list' without a prefix to discover the top-level sections. "
        "This tool is read-only: configuration cannot be changed from here, because "
        "values are bound when the process starts. Changing config means editing the "
        "YAML layer and restarting the daemon. "
        "Credentials (API keys, tokens, passwords) are always redacted: you can report "
        "whether one is set or missing, never its value."
    )
    # Disparadores multilingües SOLO para el embedding del semantic routing
    # (no van al schema del LLM). Cómo un humano pregunta por la config.
    routing_keywords = (
        "qué modelo estás usando, con qué configuración corrés, cuál es tu temperatura, "
        "qué parámetros tenés cargados, mostrame tu configuración, cómo estás configurado, "
        "qué proveedor de llm usás, tenés la api key puesta, qué credenciales te faltan, "
        "de qué fichero sale ese valor, cuál es tu zona horaria, qué base de datos usás. "
        "what model are you using, what is your configuration, show me your settings, "
        "what temperature do you run with, which llm provider are you using, "
        "do you have the api key configured, what parameters are loaded, "
        "where does that value come from, what is your timezone. "
        "quel modèle utilises-tu, quelle est ta configuration, montre-moi tes paramètres, "
        "quel fournisseur llm utilises-tu, as-tu la clé api configurée."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["get", "list"],
                "description": "The read action to perform.",
            },
            "path": {
                "type": "string",
                "description": (
                    "Dotted path of the parameter to read (required for 'get'). "
                    "Example: 'llm.model'."
                ),
            },
            "prefix": {
                "type": "string",
                "description": (
                    "With 'list': restrict to the parameters under this section. "
                    "Example: 'llm' or 'memories.consolidation'. Omit to list everything."
                ),
            },
        },
        "required": ["operation"],
    }

    def __init__(self, runtime_config: RuntimeConfigUseCase) -> None:
        self._uc = runtime_config

    def _fail(self, mensaje: str) -> ToolResult:
        # Consultar config no tiene fallos transitorios: el snapshot está en
        # memoria. Un reintento devolvería exactamente lo mismo.
        return ToolResult(
            tool_name=self.name,
            output=mensaje,
            success=False,
            error=mensaje,
            retryable=False,
        )

    @staticmethod
    def _formatear(campo: CampoRuntime) -> str:
        return f"{campo.path} = {campo.valor!r}  [{campo.origen}]"

    async def execute(self, **kwargs) -> ToolResult:
        operation = str(kwargs.get("operation") or "").strip().lower()

        if not operation:
            return self._fail("The 'operation' parameter is required.")
        if operation == "get":
            return self._get(str(kwargs.get("path") or "").strip())
        if operation == "list":
            return self._list(str(kwargs.get("prefix") or "").strip())
        return self._fail(f"Unknown operation '{operation}'. Use 'get' or 'list'.")

    def _get(self, path: str) -> ToolResult:
        if not path:
            return self._fail("'path' is required for operation 'get'.")

        campo = self._uc.get(path)
        if campo is None:
            # Decir "no existe" a secas empuja al modelo a inventar el valor que
            # no encontró. Nombrar los paths vecinos le da el siguiente paso.
            sugerencias = self._uc.sugerencias(path)
            pista = (
                f" Did you mean one of: {', '.join(sugerencias)}?"
                if sugerencias
                else " Use operation 'list' to see the available sections."
            )
            return self._fail(f"No configuration parameter at '{path}'.{pista}")

        lineas = [self._formatear(campo)]
        if campo.es_secreto:
            lineas.append(
                "This is a credential: its value is never exposed. Report only whether it is set."
            )
        return ToolResult(tool_name=self.name, output="\n".join(lineas), success=True)

    def _list(self, prefix: str) -> ToolResult:
        campos = self._uc.listar(prefix or None)
        if not campos:
            sugerencias = self._uc.sugerencias(prefix)
            pista = f" Did you mean one of: {', '.join(sugerencias)}?" if sugerencias else ""
            return self._fail(f"No configuration parameters under '{prefix}'.{pista}")

        titulo = (
            f"{len(campos)} parameter(s) under '{prefix}'"
            if prefix
            else f"{len(campos)} parameter(s) loaded"
        )
        lineas = [f"{titulo} (value [origin: which config layer set it]):"]
        lineas += [self._formatear(c) for c in campos[:_MAX_CAMPOS]]
        if len(campos) > _MAX_CAMPOS:
            lineas.append(f"... {len(campos) - _MAX_CAMPOS} more. Narrow the search with 'prefix'.")
        return ToolResult(tool_name=self.name, output="\n".join(lineas), success=True)
