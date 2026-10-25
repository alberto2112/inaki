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
# ---------------------------------------------------------------------------
RESULT_FORMAT_FOOTER = """\
You MUST end your response with a fenced JSON block in the following format, exactly:

```json
{
  "status": "success" | "failed",
  "summary": "one-sentence description of what you did",
  "details": "COMPLETE OUTPUT HERE — verbatim, not a summary",
  "reason": "failure reason code or null"
}
```

Rules for each field:
- `status`: `"success"` if you completed the task, `"failed"` if you could not.
- `summary`: one short sentence (what you did or why you failed).
- `details`: the COMPLETE output you produced — the FULL text of anything you wrote, \
generated, or computed. If the task asked you to write articles, posts, code, plans, or \
any artifact, paste the ENTIRE content here verbatim. Do NOT summarize or truncate. \
If nothing was produced, use null.
- `reason`: a short error code if status is "failed", otherwise null.

This JSON block is the LAST thing in your response. Do not add any text after the closing fence."""


@dataclass(frozen=True)
class ResultadoRendido:
    """Resultado de ``render_resultado``.

    ``content`` es el mensaje ``[bg-N] ...`` completo, tal como se persiste y se
    entrega al padre. ``result_status`` es el ``status`` que declaró el hijo en su
    bloque JSON (``None`` si no hubo bloque válido); ``parse_error`` dice que no lo
    hubo y ``content`` lleva el texto crudo.
    """

    content: str
    result_status: str | None
    parse_error: bool


def con_footer(base: str | None, *, por_defecto: str = "") -> str:
    """Devuelve el system prompt efectivo del hijo: ``base`` + footer de formato.

    Si el caller no pasó prompt (``None``) se usa ``por_defecto`` (el prompt del
    sub-agente ya resuelto). Solo ``None`` cae al default: un prompt vacío del caller
    es un prompt, igual que en el camino sync (REQ-OS-2).
    """
    prompt = base if base is not None else por_defecto
    return prompt + "\n\n" + RESULT_FORMAT_FOOTER


def render_resultado(task_id: str, raw: str) -> ResultadoRendido:
    """Parsea la respuesta final del hijo y arma el mensaje ``[bg-N] ...`` para el padre.

    Tres desenlaces (REQ-BGD-5):

    - éxito declarado → ``[bg-N] success: <summary>`` + ``details`` si hay;
    - fallo declarado por el hijo → ``[bg-N] failed (reported by the child, reason:
      <reason>): <summary>`` + ``details``;
    - sin bloque JSON válido → el texto crudo, marcado ``result_parse_error``: el
      trabajo del hijo no se pierde porque no respetó el formato.

    El mensaje va en inglés: lo lee el LLM del padre.
    """
    parsed = parse_delegation_result(raw)

    if parsed.status == "failed" and parsed.reason == "result_parse_error":
        return ResultadoRendido(
            content=(
                f"[{task_id}] completed, but without the required JSON result block "
                f"(result_parse_error). Its raw answer follows:\n\n{raw}"
            ),
            result_status=None,
            parse_error=True,
        )

    if parsed.status == "failed":
        motivo = f", reason: {parsed.reason}" if parsed.reason else ""
        cabecera = f"[{task_id}] failed (reported by the child{motivo}): {parsed.summary}"
    else:
        cabecera = f"[{task_id}] {parsed.status}: {parsed.summary}"
    content = f"{cabecera}\n\n{parsed.details}" if parsed.details else cabecera
    return ResultadoRendido(content=content, result_status=parsed.status, parse_error=False)
