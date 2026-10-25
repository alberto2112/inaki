"""Clasificación y reporte honesto de las delegaciones que fallan o se interrumpen.

Funciones PURAS (sin I/O): reciben el ``trace`` en memoria del hijo y devuelven
``DelegationFailure`` / texto. Nacen de un caso real: un ``TimeoutError`` sin mensaje
se reportaba al padre como ``failed: TimeoutError: ``, sin decir qué tool había
quedado a medias, y el padre concluía "no se ejecutó nada". Las reglas:

- El detalle del fallo NUNCA queda vacío (``Type: msg`` o ``Type (no message)``).
- Una tool call despachada sin resultado tiene efecto DESCONOCIDO: se nombra y se
  dice "outcome unknown, it may have had side effects". Jamás "no se ejecutó".
- El reporte no depende del disco: sale del ``trace`` en memoria.

Todo lo que lee el LLM del padre va en INGLÉS.
"""

from __future__ import annotations

import json
from typing import NamedTuple

from inaki.agents.delegation.workdirs import ruta_relativa
from inaki.kernel.domain.background_task import (
    BackgroundTask,
    DelegationFailure,
    DelegationFailureKind,
    InFlightToolCall,
)
from inaki.shared.errors import ToolLoopMaxIterationsError
from inaki.shared.message import Message, Role

_ARGS_PREVIEW_MAX = 200
_LAST_STEP_TEXT_MAX = 200
_PROMPT_PREVIEW_MAX = 80
_ELLIPSIS = "…"

_EFECTO_DESCONOCIDO = "outcome unknown, it may have had side effects"


class ToolsSinPar(NamedTuple):
    """Tool calls del transcript sin su resultado.

    ``en_vuelo`` es la primera sin resultado: el loop ejecuta las tools de un batch
    en secuencia, así que fue la que se estaba ejecutando cuando el hijo se cortó
    (su efecto es desconocido). ``no_iniciadas`` son las que le seguían en el batch:
    de esas SÍ se sabe que no corrieron.
    """

    en_vuelo: InFlightToolCall | None
    no_iniciadas: list[str]


def _preview(texto: str, maximo: int) -> str:
    """Recorta a ``maximo`` caracteres (elipsis incluida)."""
    if len(texto) <= maximo:
        return texto
    return texto[: maximo - 1] + _ELLIPSIS


def _nombre_de(tool_call: dict) -> str:
    return tool_call.get("function", {}).get("name", "") or "?"


def _args_preview(tool_call: dict) -> str:
    args = tool_call.get("function", {}).get("arguments", "")
    texto = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
    return _preview(texto, _ARGS_PREVIEW_MAX)


def tool_calls_sin_par(trace: list[Message]) -> ToolsSinPar:
    """Separa las tool calls sin resultado en "en vuelo" y "no iniciadas".

    Opera sobre el transcript CRUDO (REQ-BGP-2): el pareo ``tool_call_id`` es la
    evidencia, no se normaliza ni se descartan grupos incompletos.
    """
    con_resultado = {m.tool_call_id for m in trace if m.role is Role.TOOL and m.tool_call_id}
    sin_par = [
        tc
        for m in trace
        if m.role is Role.ASSISTANT and m.tool_calls
        for tc in m.tool_calls
        if tc.get("id", "") not in con_resultado
    ]
    if not sin_par:
        return ToolsSinPar(en_vuelo=None, no_iniciadas=[])
    primera = sin_par[0]
    return ToolsSinPar(
        en_vuelo=InFlightToolCall(
            tool_call_id=primera.get("id", ""),
            name=_nombre_de(primera),
            arguments_preview=_args_preview(primera),
        ),
        no_iniciadas=[_nombre_de(tc) for tc in sin_par[1:]],
    )


def ultimo_paso(trace: list[Message]) -> str:
    """Describe en una frase qué fue lo último que hizo el hijo."""
    if not trace:
        return "no LLM response yet"
    ultimo = trace[-1]
    if ultimo.role is Role.ASSISTANT and ultimo.tool_calls:
        return "called " + ", ".join(_nombre_de(tc) for tc in ultimo.tool_calls)
    if ultimo.role is Role.TOOL:
        return f"received the result of {_tool_de_resultado(trace, ultimo)}"
    if ultimo.role is Role.ASSISTANT and ultimo.content.strip():
        return f"said: «{_preview(ultimo.content, _LAST_STEP_TEXT_MAX)}»"
    return "no LLM response yet"


def _tool_de_resultado(trace: list[Message], resultado: Message) -> str:
    """Nombre de la tool a la que responde ``resultado`` (``a tool`` si no se halla)."""
    for m in trace:
        if m.role is Role.ASSISTANT and m.tool_calls:
            for tc in m.tool_calls:
                if tc.get("id", "") == resultado.tool_call_id:
                    return _nombre_de(tc)
    return "a tool"


def _detalle_de(exc: BaseException) -> str:
    """``Type: msg`` o ``Type (no message)`` — nunca vacío."""
    tipo = type(exc).__name__
    mensaje = str(exc).strip()
    return f"{tipo}: {mensaje}" if mensaje else f"{tipo} (no message)"


def clasificar_fallo(
    exc: BaseException, trace: list[Message], task: BackgroundTask, elapsed: float
) -> DelegationFailure:
    """Clasifica el fallo del hijo en ``timeout`` / ``max_iterations`` / ``exception``.

    ``elapsed`` son los segundos transcurridos desde que el hijo empezó a correr.
    Las iteraciones usadas se cuentan por respuestas del LLM en el ``trace`` (un
    timeout no trae ese dato); en ``max_iterations`` es el tope por definición.
    """
    sin_par = tool_calls_sin_par(trace)
    iteraciones = sum(1 for m in trace if m.role is Role.ASSISTANT)
    last_step = ultimo_paso(trace)

    kind: DelegationFailureKind
    if isinstance(exc, ToolLoopMaxIterationsError):
        kind = "max_iterations"
        iteraciones = task.max_iterations
        detalle = (
            f"ToolLoopMaxIterationsError: reached the limit of {task.max_iterations} iterations"
        )
        if exc.last_response.strip():
            last_step = f"said: «{_preview(exc.last_response, _LAST_STEP_TEXT_MAX)}»"
    elif isinstance(exc, TimeoutError):
        kind = "timeout"
        detalle = _detalle_de(exc)
    else:
        kind = "exception"
        detalle = _detalle_de(exc)

    return DelegationFailure(
        kind=kind,
        detail=detalle,
        iterations_used=iteraciones,
        elapsed_seconds=int(elapsed),
        last_step=last_step,
        in_flight_tool=sin_par.en_vuelo,
        not_started_tools=sin_par.no_iniciadas,
    )


def fallo_unknown_target(target_agent_id: str) -> DelegationFailure:
    """Fallo de una delegación cuyo destino no es un sub-agente conocido.

    El hijo nunca arrancó: no hay transcript ni tool en vuelo.
    """
    return DelegationFailure(
        kind="unknown_target", detail=f"'{target_agent_id}' is not a known sub-agent"
    )


_TITULARES = {
    "exception": "the child agent stopped with an error.",
    "interrupted": "the daemon stopped while the child was working (reload or shutdown).",
}


def _titular(task: BackgroundTask, falla: DelegationFailure) -> str:
    if falla.kind == "timeout":
        return f"the child agent ran out of time ({task.timeout_seconds}s) before finishing."
    if falla.kind == "max_iterations":
        return f"the child agent used all {task.max_iterations} iterations without a final answer."
    if falla.kind == "unknown_target":
        return f"'{task.target_agent_id}' is not a known sub-agent."
    return _TITULARES[falla.kind]


def render_fallo(task: BackgroundTask, falla: DelegationFailure) -> str:
    """Mensaje ``[bg-N] failed (...)`` que recibe el padre. Las líneas sin dato se omiten."""
    cabecera = f"[{task.id}] failed ({falla.kind}): {_titular(task, falla)}"
    if falla.kind == "unknown_target":
        # Nunca corrió: no hay presupuesto gastado ni transcript que inspeccionar.
        return cabecera

    lineas = [
        cabecera,
        f"- agent: {task.target_agent_id}",
        f"- budget: {task.timeout_seconds}s / {task.max_iterations} iterations"
        f" — used {falla.elapsed_seconds}s / {falla.iterations_used} iterations",
    ]
    if falla.last_step:
        lineas.append(f"- last step: {falla.last_step}")
    if falla.in_flight_tool is not None:
        tool = falla.in_flight_tool
        lineas.append(
            f"- tool call in flight: {tool.name}({tool.arguments_preview})"
            f" — {_EFECTO_DESCONOCIDO}. Verify before retrying."
        )
    if falla.not_started_tools:
        lineas.append(f"- not started: {', '.join(falla.not_started_tools)}")
    if falla.kind in ("exception", "interrupted"):
        # En timeout/max_iterations el titular ya lo explica; el detalle sería ruido.
        lineas.append(f"- error: {falla.detail}")
    if task.workdir is not None:
        # Lo que el hijo alcanzó a producir sigue ahí: el padre puede rescatarlo (REQ-BGW-10).
        lineas.append(
            f"- workdir: {ruta_relativa(task.workdir)} — partial outputs may be there; "
            "inspect lists its contents."
        )
    lineas.append(
        f"Inspect the full transcript with background_tasks(action=inspect, task_id={task.id})."
    )
    return "\n".join(lineas)


def _prompt_entre_comillas(task: BackgroundTask) -> str:
    return f'"{_preview(task.prompt, _PROMPT_PREVIEW_MAX)}"'


def _cuanto_corrio(task: BackgroundTask) -> str:
    if task.run_started_at is None:
        return "never started (it was queued)"
    if task.finished_at is None:
        return "was running when it was cut off"
    segundos = int((task.finished_at - task.run_started_at).total_seconds())
    tramo = f"was running for {segundos}s"
    if task.iterations_used is not None:
        plural = "" if task.iterations_used == 1 else "s"
        tramo += f", {task.iterations_used} iteration{plural}"
    return tramo


def _linea_interrumpida(task: BackgroundTask) -> str:
    partes = [
        f"- {task.id} → {task.target_agent_id}",
        _cuanto_corrio(task),
        _prompt_entre_comillas(task),
    ]
    falla = task.failure
    if falla is not None and falla.last_step:
        partes.append(f"last step: {falla.last_step}")
    if falla is not None and falla.in_flight_tool is not None:
        partes.append(f"tool call in flight: {falla.in_flight_tool.name} — {_EFECTO_DESCONOCIDO}")
    partes.extend(_sufijo_workdir(task))
    return " | ".join(partes)


def _sufijo_workdir(task: BackgroundTask) -> list[str]:
    """Pieza ``workdir: dwss/<n>/`` de la línea de una task, o nada si no tiene."""
    return [f"workdir: {ruta_relativa(task.workdir)}"] if task.workdir is not None else []


def _linea_entrega_barrida(task: BackgroundTask) -> str:
    return " | ".join(
        [
            f"- {task.id} → {task.target_agent_id}",
            "finished, but its delivery was interrupted (it may or may not have reached you)",
            _prompt_entre_comillas(task),
            f"retrieve its result with background_tasks(action=cancel, task_id={task.id})",
            *_sufijo_workdir(task),
        ]
    )


def _ids(tasks: list[BackgroundTask]) -> str:
    return ", ".join(t.id for t in tasks)


def render_aviso_interrupcion(
    interrumpidas: list[BackgroundTask], entregas_barridas: list[BackgroundTask]
) -> str:
    """Aviso ÚNICO que se le manda al padre tras un restart (REQ-BGD-11).

    ``interrumpidas`` son las que el restart cortó a medias; ``entregas_barridas`` las
    que habían terminado pero cuya entrega quedó a medias (``delivering`` →
    ``delivery_failed``): su resultado está guardado y se recupera con ``cancel``. Van
    en un bloque aparte porque no son lo mismo: ahí no se perdió trabajo, se perdió
    la certeza de que llegó.
    """
    if not interrumpidas and not entregas_barridas:
        raise ValueError("render_aviso_interrupcion: no hay nada que avisar")

    bloques: list[str] = []
    if interrumpidas:
        bloques.append(
            f"[{_ids(interrumpidas)}] interrupted: the daemon restarted or reloaded while "
            "these background delegations were in progress. They will NOT deliver a result "
            "on their own.\n" + "\n".join(_linea_interrumpida(t) for t in interrumpidas)
        )
    if entregas_barridas:
        bloques.append(
            f"[{_ids(entregas_barridas)}] finished, result not delivered: the daemon stopped "
            "while handing their result over.\n"
            + "\n".join(_linea_entrega_barrida(t) for t in entregas_barridas)
        )

    cierre = (
        "Inspect any of them with background_tasks(action=inspect, task_id=...). "
        "Tell the user what was lost."
    )
    if interrumpidas:
        cierre += (
            " Relaunch only if it is still needed and safe: a relaunch repeats any side effects."
        )
    return "\n".join(bloques) + "\n" + cierre
