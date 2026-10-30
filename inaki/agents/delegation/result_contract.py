"""Contrato de resultado de una delegación: footer de formato + render al padre.

Lo comparten los dos caminos de delegación, para que un hijo se comporte igual
corra en el turno del padre (``delegate`` sync) o en background (``wait=false``):

- ``RESULT_FORMAT_FOOTER`` / ``con_footer``: instrucciones que se le agregan al
  system prompt del hijo para que cierre con un bloque ```json``` parseable. Sin el
  footer el hijo no tiene contrato y ``parse_delegation_result`` devuelve
  ``result_parse_error`` siempre (REQ-DG-11).
- ``render_resultado``: parsea la respuesta del hijo y arma el mensaje ``[bg-N] ...``
  que se le inyecta al padre (REQ-BGD-5). Función pura.

Vive en ``agents`` y no junto al parser del kernel: el footer es política de
delegación, y sus dos consumidores (``delegate_tool`` y ``background_queue``) están
en este paquete.
"""

from __future__ import annotations

from dataclasses import dataclass

from inaki.kernel._result_parser import parse_delegation_result

# ---------------------------------------------------------------------------
# Result-format footer
#
# Se agrega al final de todo system prompt efectivo que recibe el hijo. Le exige
# cerrar su respuesta con un bloque ```json``` con los campos requeridos, que
# ``parse_delegation_result`` extrae. El texto va en INGLÉS: lo lee el LLM.
#
# Plantilla ÚNICA con dos variantes: el hijo sync (``wait=true``) solo puede cerrar con
# ``success``/``failed``; el background además puede PAUSAR con ``needs_input`` + una
# pregunta (``delegate(resume=...)`` la retoma). Sin transcript durable, el sync no tiene
# de dónde reanudar: no se le ofrece.
# ---------------------------------------------------------------------------
_FOOTER_APERTURA = """\
You MUST end your response with a fenced JSON block in the following format, exactly:

```json
{
  "status": %(status)s,
  "summary": "one-sentence description of what you did",
  "details": "COMPLETE OUTPUT HERE — verbatim, not a summary",
  "reason": "failure reason code or null"%(campo_question)s
}
```

Rules for each field:
- `status`: `"success"` if you completed the task, `"failed"` if you could not.%(regla_status)s
- `summary`: one short sentence (what you did or why you failed).
- `details`: the COMPLETE output you produced — the FULL text of anything you wrote, \
generated, or computed. If the task asked you to write articles, posts, code, plans, or \
any artifact, paste the ENTIRE content here verbatim. Do NOT summarize or truncate. \
If nothing was produced, use null.
- `reason`: a short error code if status is "failed", otherwise null.%(regla_question)s

This JSON block is the LAST thing in your response. Do not add any text after the closing fence."""

_STATUS_SYNC = '"success" | "failed"'
_STATUS_PAUSABLE = '"success" | "failed" | "needs_input"'
_CAMPO_QUESTION = ',\n  "question": "your ONE question, only with needs_input, otherwise null"'
_REGLA_STATUS_PAUSABLE = (
    ' Use `"needs_input"` ONLY if you cannot continue without information that only the '
    "user or the delegating agent can provide. Your work so far is kept and you will "
    "continue this same task with the answer. Never use it to ask permission for "
    "something you can decide yourself."
)
_REGLA_QUESTION = (
    "\n- `question`: required with `needs_input` (ONE precise question), otherwise null."
)


def _armar_footer(*, puede_pausar: bool) -> str:
    """Instancia la plantilla del footer: con o sin la pausa ``needs_input``."""
    if not puede_pausar:
        return _FOOTER_APERTURA % {
            "status": _STATUS_SYNC,
            "campo_question": "",
            "regla_status": "",
            "regla_question": "",
        }
    return _FOOTER_APERTURA % {
        "status": _STATUS_PAUSABLE,
        "campo_question": _CAMPO_QUESTION,
        "regla_status": _REGLA_STATUS_PAUSABLE,
        "regla_question": _REGLA_QUESTION,
    }


# Footer del camino sync (y de cualquier hijo que no pueda pausar). Texto sin cambios.
RESULT_FORMAT_FOOTER = _armar_footer(puede_pausar=False)

# Footer del hijo background: ofrece ``needs_input`` + ``question``.
RESULT_FORMAT_FOOTER_PAUSABLE = _armar_footer(puede_pausar=True)


@dataclass(frozen=True)
class ResultadoRendido:
    """Resultado de ``render_resultado``.

    ``content`` es el mensaje ``[bg-N] ...`` completo, tal como se persiste y se
    entrega al padre. ``result_status`` es el ``status`` que declaró el hijo en su
    bloque JSON (``None`` si no hubo bloque válido); ``parse_error`` dice que no lo
    hubo y ``content`` lleva el texto crudo.

    ``question`` y ``details`` solo vienen cuando el hijo PAUSÓ (``needs_input`` con
    pregunta, en un render que admite pausa): la pregunta sin espacios sobrantes y lo que
    el hijo dejó dicho. Con ellos la cola decide si pausa de verdad o degrada a
    ``render_pregunta_no_reanudable``.
    """

    content: str
    result_status: str | None
    parse_error: bool
    question: str | None = None
    details: str | None = None


def con_footer(base: str | None, *, por_defecto: str = "", puede_pausar: bool = False) -> str:
    """Devuelve el system prompt efectivo del hijo: ``base`` + footer de formato.

    Si el caller no pasó prompt (``None``) se usa ``por_defecto`` (el prompt del
    sub-agente ya resuelto). Solo ``None`` cae al default: un prompt vacío del caller
    es un prompt, igual que en el camino sync (REQ-OS-2).

    ``puede_pausar`` elige la variante del footer: solo el hijo background (que tiene
    transcript durable de donde reanudar) puede cerrar con ``needs_input``.
    """
    prompt = base if base is not None else por_defecto
    footer = RESULT_FORMAT_FOOTER_PAUSABLE if puede_pausar else RESULT_FORMAT_FOOTER
    return prompt + "\n\n" + footer


def _contenido_de_error_de_formato(task_id: str, raw: str) -> ResultadoRendido:
    """El hijo terminó pero sin un bloque JSON de resultado válido: se entrega el crudo."""
    return ResultadoRendido(
        content=(
            f"[{task_id}] completed, but without the required JSON result block "
            f"(result_parse_error). Its raw answer follows:\n\n{raw}"
        ),
        result_status=None,
        parse_error=True,
    )


def render_resultado(
    task_id: str,
    raw: str,
    *,
    puede_pausar: bool = False,
    workdir: str | None = None,
) -> ResultadoRendido:
    """Parsea la respuesta final del hijo y arma el mensaje ``[bg-N] ...`` para el padre.

    Cuatro desenlaces (REQ-BGD-5, REQ-RES-1):

    - éxito declarado → ``[bg-N] success: <summary>`` + ``details`` si hay;
    - fallo declarado por el hijo → ``[bg-N] failed (reported by the child, reason:
      <reason>): <summary>`` + ``details``;
    - pausa (solo con ``puede_pausar``): ``needs_input`` con una ``question`` no vacía →
      ``[bg-N] needs_input: <question>`` + cómo continuar (ver ``_render_pausa``).
      ``needs_input`` SIN pregunta, o en blanco, no pausa: es ``result_parse_error``;
    - sin bloque JSON válido → el texto crudo, marcado ``result_parse_error``: el
      trabajo del hijo no se pierde porque no respetó el formato.

    ``workdir`` es la ruta relativa (``dwss/<n>/``) del directorio de trabajo de la task,
    si tiene; solo la usa el render de la pausa. El resto de los render recibe el aviso de
    directorio de parte de la cola. El mensaje va en inglés: lo lee el LLM del padre.
    """
    parsed = parse_delegation_result(raw)

    if parsed.status == "failed" and parsed.reason == "result_parse_error":
        return _contenido_de_error_de_formato(task_id, raw)

    if puede_pausar and parsed.status == "needs_input":
        pregunta = (parsed.question or "").strip()
        if not pregunta:
            return _contenido_de_error_de_formato(task_id, raw)
        return ResultadoRendido(
            content=_render_pausa(task_id, pregunta, parsed.details, workdir),
            result_status="needs_input",
            parse_error=False,
            question=pregunta,
            details=parsed.details,
        )

    if parsed.status == "failed":
        motivo = f", reason: {parsed.reason}" if parsed.reason else ""
        cabecera = f"[{task_id}] failed (reported by the child{motivo}): {parsed.summary}"
    else:
        cabecera = f"[{task_id}] {parsed.status}: {parsed.summary}"
    content = f"{cabecera}\n\n{parsed.details}" if parsed.details else cabecera
    return ResultadoRendido(content=content, result_status=parsed.status, parse_error=False)


def _render_pausa(task_id: str, pregunta: str, details: str | None, workdir: str | None) -> str:
    """Mensaje ``[bg-N] needs_input: <question>`` de un hijo que quedó esperando respuesta.

    Le da al padre las dos vías para continuar (REQ-RES-15): responder él mismo si la sabe,
    o preguntarle al usuario y reanudar con su respuesta; y le prohíbe re-delegar, que
    perdería el trabajo ya hecho.
    """
    partes = [f"[{task_id}] needs_input: {pregunta}"]
    if details:
        partes.append(details)
    partes.append(
        "The child agent paused and keeps all its work so far. If you know the answer, "
        f'resume it yourself with delegate(resume="{task_id}", task="<answer>"). '
        "Otherwise ask the user and resume with their reply. "
        "Do NOT start a new delegation for this."
    )
    if workdir is not None:
        partes.append(f"Working directory: {workdir} (deleted when this task is pruned).")
    return "\n\n".join(partes)


def render_pregunta_no_reanudable(task_id: str, rendido: ResultadoRendido, motivo: str) -> str:
    """Mensaje para una pregunta del hijo que NO se puede reanudar (REQ-RES-3).

    Se usa cuando la pausa no es posible (snapshot fallido, store degradado, recorder
    incompleto, sin iteraciones restantes): el padre igual recibe la pregunta y lo que el
    hijo dejó dicho, pero con la verdad por delante: no hay continuación. Nunca promete
    ``delegate(resume=...)``; sugiere una delegación NUEVA que incluya la respuesta.
    """
    pregunta = rendido.question or ""
    partes = [f"[{task_id}] needs_input (NOT resumable: {motivo}): {pregunta}"]
    if rendido.details:
        partes.append(rendido.details)
    partes.append(
        "This task cannot be resumed. If the answer is still needed, ask the user and start "
        "a NEW delegation that includes the answer and the context above."
    )
    return "\n\n".join(partes)
