"""ConfigSetTool — el operador le pide al agente que cambie un parámetro de su propia config.

Es la mitad escritora de ``config`` (que es de solo lectura) y es PRIVILEGIADA:
declara ``requires_operator = True`` y el kernel la rechaza en cualquier turno que no
sea de operador (``run_tool_loop``). La tool NO decide su privilegio: lee el
``ChannelContext`` solo para saber a qué chat avisar cuando el daemon vuelva, nunca
para autorizarse (la delegación sync corre dentro del turno del padre y heredaría ese
contexto).

No muta nada: delega en ``GestionarOverridesUseCase``, que valida el cambio con el
loader del arranque, lo guarda en la capa de overrides EN MEMORIA y pide el reload que
lo aplica reconstruyendo el daemon. Persistir sigue siendo escribir la capa YAML.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from inaki.config.use_cases.runtime_overrides import (
    EstadoSet,
    GestionarOverridesUseCase,
    MotivoRechazo,
    ResultadoSet,
)
from inaki.kernel.ports.tool_port import ITool, ToolResult
from inaki.shared.channel_context import ChannelContext

_RECHAZADO = "Rejected, nothing was changed: "

_REINICIO = (
    "It applies when the daemon restarts right after this reply{confirmacion}. "
    "The restart also loads any edit made to the config files since the last start. "
    "Kept in memory only: a manual /reload, a service restart or a failed restart "
    "discards it."
)


class ConfigSetTool(ITool):
    name = "config_set"
    requires_operator = True
    description = (
        "Change one of your own configuration parameters at runtime. Operator only: it "
        "runs only when the turn comes from the operator (for example, the operator in a "
        "private chat); otherwise it is refused. Needs 'path' and 'value'. Paths are dotted, as in the 'config' tool: "
        "'llm.model', 'llm.temperature', 'tools.semantic_routing_top_k'. The change is "
        "validated first and kept in memory only (never written to the config files). It "
        "takes effect when the daemon restarts, which happens automatically right after "
        "this reply; a manual /reload, a service restart or a failed restart discards ALL "
        "runtime changes (a manual /reload is the way to undo them). To undo a single "
        "change, set the value you want. Credentials, the system prompt, workspace "
        "containment, the embedding settings, file paths and databases, channels, "
        "providers and the admin server cannot be changed here. Use the 'config' tool to "
        "read the current value first: values changed here show the origin [runtime]."
    )
    routing_keywords = (
        "cambiá tu modelo a, usá otro modelo, poné la temperatura en, bajá la temperatura, "
        "subí el límite de tokens, cambiá la configuración, desactivá la consolidación de "
        "memoria, cambiá tu zona horaria. "
        "change your model to, switch to another model, set the temperature to, "
        "change your configuration, disable memory consolidation, change your timezone. "
        "change de modèle, mets la température à, modifie ta configuration."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Dotted path of the parameter to change. Example: 'llm.model'.",
            },
            "value": {
                "type": ["string", "number", "integer", "boolean", "array", "object", "null"],
                "description": (
                    "New value. A map changes only the keys you give; a list replaces the "
                    "whole list; null disables the parameter or section."
                ),
            },
        },
        "required": ["path", "value"],
    }

    def __init__(
        self,
        uc: GestionarOverridesUseCase,
        *,
        get_channel_context: Callable[[], ChannelContext | None],
        tiene_outbound: Callable[[str], bool],
    ) -> None:
        self._uc = uc
        self._get_channel_context = get_channel_context
        self._tiene_outbound = tiene_outbound

    async def execute(self, **kwargs: Any) -> ToolResult:
        path = str(kwargs.get("path") or "").strip()
        if not path:
            return self._fail("'path' is required.")
        if "value" not in kwargs:
            # `null` es un valor (desactiva); la ausencia es un error del LLM.
            return self._fail("'value' is required (use null to disable).")

        aviso_a = self._chat_al_que_avisar()
        resultado = self._uc.set(path, kwargs["value"], aviso_a=aviso_a)
        return self._traducir(resultado, avisara=aviso_a is not None)

    def _chat_al_que_avisar(self) -> tuple[str, str] | None:
        """``(canal, chat_id)`` del turno, solo si ese canal puede mandar el aviso después.

        REST/CLI ya vieron el output de la tool; sin contexto (gateway) no hay a quién.
        """
        contexto = self._get_channel_context()
        if contexto is None or not self._tiene_outbound(contexto.channel_type):
            return None
        return contexto.channel_type, contexto.context_id

    def _fail(self, mensaje: str) -> ToolResult:
        # Un reintento con los mismos argumentos devolvería lo mismo: el LLM tiene que
        # cambiar el pedido, no repetirlo.
        return ToolResult(
            tool_name=self.name, output=mensaje, success=False, error=mensaje, retryable=False
        )

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
