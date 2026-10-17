"""ConfigTool — el agente consulta su propia configuración y, a pedido del operador, la cambia.

Responde "¿con qué parámetros estoy corriendo?" sobre el snapshot que el
proceso tiene CARGADO, no sobre los YAML. La diferencia importa: entre lo
escrito y lo cargado hay un reinicio de por medio, y además el schema coerce
valores al validar. Un agente que leyera los ficheros contestaría con
seguridad algo que no es lo que está ejecutando.

Es UNA tool con privilegio POR OPERACIÓN: ``get`` y ``list`` (lectura) corren en
cualquier turno; ``set`` solo en un turno del operador (``requires_operator``,
``unlocked_operations={"get", "list"}``; el kernel lo hace cumplir en ``run_tool_loop``).
Antes eran dos tools (``config`` y ``config_set``); un historial viejo puede mencionar
``config_set``. En el resto del código ``config set`` abrevia la operación ``set`` de esta tool.

``set`` solo existe cuando hay de dónde aplicarlo (daemon + agente regular, con el store
de overrides y el reloader): sin ``escritura`` la tool se construye SOLO lectura (enum
``get``/``list``, no privilegiada). Por eso el schema, la descripción y el privilegio son
atributos de INSTANCIA fijados en ``__init__``; ``ToolRegistry.register`` los valida igual.

MUTAR un parámetro en caliente no existe como concepto en este sistema: los providers y los
use cases reciben sus valores al construirse (``ResolvedLLMConfig``, los Settings VO
``frozen=True``), así que mutar el objeto de config no cambiaría el comportamiento de la
siguiente llamada — solo haría que la tool y el runtime discrepen. Lo que sí existe es
CAMBIAR la config reconstruyendo el daemon: ``set`` delega en ``GestionarOverridesUseCase``,
que valida el cambio con el loader del arranque, lo guarda en la capa de overrides EN
MEMORIA y pide el reload que lo aplica. Los valores pisados así llevan el origen
``runtime`` y se pierden en un ``/reload`` manual o un reinicio. Persistir un cambio es
escribir la capa YAML y recargar el daemon (``inaki reload``). La tool NO decide su
privilegio: lee el ``ChannelContext`` solo para saber a qué chat avisar cuando el daemon
vuelva, nunca para autorizarse (la delegación sync corre dentro del turno del padre y
heredaría ese contexto).

Las credenciales NO salen de acá con ningún parámetro: el snapshot que recibe
esta tool ya viene redactado desde ``RuntimeConfigUseCase``. De un secreto se
puede saber si está puesto o si falta, nunca su valor.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from inaki.config.use_cases.runtime_config import (
    ORIGEN_RUNTIME,
    CampoRuntime,
    RuntimeConfigUseCase,
)
from inaki.config.use_cases.runtime_overrides import (
    EstadoSet,
    GestionarOverridesUseCase,
    MotivoRechazo,
    ResultadoSet,
)
from inaki.kernel.ports.tool_port import ITool, ToolResult
from inaki.shared.channel_context import ChannelContext

_MAX_CAMPOS = 120
"""Techo de campos por ``list``. La config entera son ~90 campos y las
extensiones la agrandan; volcarla completa en cada consulta se come el
presupuesto de tokens del turno para responder una pregunta puntual."""


_RECHAZADO = "Rejected, nothing was changed: "

_REINICIO = (
    "It applies when the daemon restarts right after this reply{confirmacion}. "
    "The restart also loads any edit made to the config files since the last start. "
    "Kept in memory only: a manual /reload, a service restart or a failed restart "
    "discards it."
)

_OPERACIONES_LECTURA = ("get", "list")
_OPERACION_ESCRITURA = "set"


@dataclass(frozen=True)
class EscrituraDeConfig:
    """Lo que necesita la operación ``set``: de dónde aplicarla y a quién avisar."""

    uc: GestionarOverridesUseCase
    get_channel_context: Callable[[], ChannelContext | None]
    tiene_outbound: Callable[[str], bool]


_DESCRIPCION_LECTURA = (
    "Read the agent's own live configuration: the parameters currently loaded "
    "in memory, not what is written in the config files. "
    "Required parameter: 'operation', one of: "
    "'get' (read one parameter; needs 'path'), "
    "'list' (show the parameters under a section; optional 'prefix'). "
    "Paths are dotted, e.g. 'llm.model', 'llm.temperature', 'memories.db_filename', "
    "'tools.semantic_routing_top_k'. There is no 'global.' or 'agent.' prefix: "
    "those are the config LAYER a value comes from, which is reported as its origin. "
    "Use 'list' without a prefix to discover the top-level sections. "
)

_DESCRIPCION_SOLO_LECTURA = (
    _DESCRIPCION_LECTURA
    + "This tool is read-only. Values marked [runtime] were changed at runtime and are "
    "lost on a manual reload or a restart. "
)

_DESCRIPCION_CON_ESCRITURA = (
    _DESCRIPCION_LECTURA + "Reading ('get', 'list') is open to everyone. "
    "'set' CHANGES one of your own parameters at runtime (needs 'path' and 'value'): it is "
    "Operator only, it runs only when the turn comes from the operator (for example, the "
    "operator in a private chat); otherwise it is refused. "
    "The change is validated first and kept in memory only (never written to the config "
    "files). It takes effect when the daemon restarts, which happens automatically right "
    "after this reply; a manual /reload, a service restart or a failed restart discards ALL "
    "runtime changes (a manual /reload is the way to undo them). To undo a single change, "
    "set the value you want. Credentials, the system prompt, workspace containment, the "
    "embedding settings, file paths and databases, channels, providers and the admin server "
    "cannot be changed with 'set'. Read the current value first: values changed with 'set' "
    "show the origin [runtime]. "
)

_DESCRIPCION_CREDENCIALES = (
    "Credentials (API keys, tokens, passwords) are always redacted: you can report "
    "whether one is set or missing, never its value."
)

# Disparadores multilingües SOLO para el embedding del semantic routing
# (no van al schema del LLM). Cómo un humano pregunta por la config.
_KEYWORDS_LECTURA = (
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

_KEYWORDS_ESCRITURA = (
    " cambiá tu modelo a, usá otro modelo, poné la temperatura en, bajá la temperatura, "
    "subí el límite de tokens, cambiá la configuración, desactivá la consolidación de "
    "memoria, cambiá tu zona horaria. "
    "change your model to, switch to another model, set the temperature to, "
    "change your configuration, disable memory consolidation, change your timezone. "
    "change de modèle, mets la température à, modifie ta configuration."
)

_SCHEMA_BASE: dict[str, Any] = {
    "type": "object",
    "properties": {
        "operation": {
            "type": "string",
            "enum": list(_OPERACIONES_LECTURA),
            "description": "The action to perform.",
        },
        "path": {
            "type": "string",
            "description": (
                "Dotted path of the parameter ('get' and 'set' require it). Example: 'llm.model'."
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

_PROPIEDAD_VALUE: dict[str, Any] = {
    "type": ["string", "number", "integer", "boolean", "array", "object", "null"],
    "description": (
        "With 'set': the new value. A map changes only the keys you give; a list replaces "
        "the whole list; null disables the parameter or section."
    ),
}


class ConfigTool(ITool):
    name = "config"
    operation_param = "operation"

    def __init__(
        self,
        runtime_config: RuntimeConfigUseCase,
        *,
        escritura: EscrituraDeConfig | None = None,
    ) -> None:
        self._uc = runtime_config
        self._escritura = escritura
        # Atributos de INSTANCIA: con o sin `set` la tool es otra (schema, privilegio).
        schema = copy.deepcopy(_SCHEMA_BASE)
        if escritura is None:
            self.description = _DESCRIPCION_SOLO_LECTURA + _DESCRIPCION_CREDENCIALES
            self.routing_keywords = _KEYWORDS_LECTURA
        else:
            schema["properties"]["operation"]["enum"].append(_OPERACION_ESCRITURA)
            schema["properties"]["value"] = copy.deepcopy(_PROPIEDAD_VALUE)
            self.description = _DESCRIPCION_CON_ESCRITURA + _DESCRIPCION_CREDENCIALES
            self.routing_keywords = _KEYWORDS_LECTURA + _KEYWORDS_ESCRITURA
            self.requires_operator = True
            self.unlocked_operations = frozenset(_OPERACIONES_LECTURA)
        self.parameters_schema = schema

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

    async def execute(self, **kwargs: Any) -> ToolResult:
        # Despacho y autorización salen de la MISMA función (`operation_of`).
        operation = self.operation_of(kwargs)

        if operation is None:
            return self._fail("The 'operation' parameter is required.")
        if operation == "get":
            return self._get(str(kwargs.get("path") or "").strip())
        if operation == "list":
            return self._list(str(kwargs.get("prefix") or "").strip())
        if operation == _OPERACION_ESCRITURA and self._escritura is not None:
            return self._set(kwargs)
        validas = (
            "Use 'get', 'list' or 'set'." if self._escritura is not None else "Use 'get' or 'list'."
        )
        return self._fail(f"Unknown operation '{operation}'. {validas}")

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
        if campo.origen == ORIGEN_RUNTIME:
            lineas.append(
                "This value was changed at runtime with operation 'set'. It is kept in memory "
                "only: a manual /reload, a service restart or a failed restart discards it."
            )
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

    def _set(self, kwargs: dict[str, Any]) -> ToolResult:
        assert self._escritura is not None  # lo garantiza `execute`
        path = str(kwargs.get("path") or "").strip()
        if not path:
            return self._fail("'path' is required.")
        if "value" not in kwargs:
            # `null` es un valor (desactiva); la ausencia es un error del LLM.
            return self._fail("'value' is required (use null to disable).")

        aviso_a = self._chat_al_que_avisar()
        resultado = self._escritura.uc.set(path, kwargs["value"], aviso_a=aviso_a)
        return self._traducir(resultado, avisara=aviso_a is not None)

    def _chat_al_que_avisar(self) -> tuple[str, str] | None:
        """``(canal, chat_id)`` del turno, solo si ese canal puede mandar el aviso después.

        REST/CLI ya vieron el output de la tool; sin contexto (gateway) no hay a quién.
        """
        assert self._escritura is not None
        contexto = self._escritura.get_channel_context()
        if contexto is None or not self._escritura.tiene_outbound(contexto.channel_type):
            return None
        return contexto.channel_type, contexto.context_id

    def _traducir(self, r: ResultadoSet, *, avisara: bool) -> ToolResult:
        if r.estado is EstadoSet.ACEPTADO:
            confirmacion = "; you will get a confirmation here when it is back" if avisara else ""
            salida = f"Accepted: {r.path} = {r.valor!r} (was {r.anterior!r}). " + _REINICIO.format(
                confirmacion=confirmacion
            )
            return ToolResult(tool_name=self.name, output=salida, success=True)
        if r.estado is EstadoSet.SIN_CAMBIO:
            return ToolResult(
                tool_name=self.name,
                output=f"{r.path} is already {r.valor!r}. Nothing to change.",
                success=True,
            )
        return self._fail(self._motivo(r))

    @staticmethod
    def _motivo(r: ResultadoSet) -> str:
        motivo = r.motivo
        if motivo is MotivoRechazo.RELOAD_MANUAL:
            return (
                "A manual reload is in progress and runtime changes are being discarded. "
                "Try again after the restart."
            )
        if motivo is MotivoRechazo.PATH_FIJO:
            return (
                f"'{r.path}' cannot be changed at runtime: it is a protected setting "
                "(credentials, file paths and databases, the system prompt, workspace "
                "containment, embedding, channels, providers or the admin server). "
                "Edit the config files and restart instead."
            )
        if motivo is MotivoRechazo.PATH_DESCONOCIDO:
            pista = (
                f" Did you mean one of: {', '.join(r.sugerencias)}?"
                if r.sugerencias
                else " Use the 'config' tool (operation 'list') to see the available sections."
            )
            return f"No configuration parameter at '{r.path}'.{pista}"
        if motivo is MotivoRechazo.CONFIG_INVALIDA:
            return f"{_RECHAZADO}the configuration would not load with this change. {r.detalle}"
        if motivo is MotivoRechazo.CONSTRUCCION_RECHAZADA:
            return f"{_RECHAZADO}{r.detalle}"
        if motivo is MotivoRechazo.DISCO_ROTO:
            return (
                f"{_RECHAZADO}the config files on disk do not load right now "
                f"(were they edited without a reload?). {r.detalle}"
            )
        return f"{_RECHAZADO}{r.detalle}"
