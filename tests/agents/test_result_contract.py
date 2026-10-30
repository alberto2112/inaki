"""Tests de ``result_contract``: footer de formato y render del resultado async.

REQ-BGD-5 / REQ-DG-11: el hijo async recibe el MISMO footer JSON que el sync y su
respuesta se parsea con ``parse_delegation_result``; el padre recibe el resultado
estructurado como ``[bg-N] ...`` o, si el hijo no respetó el formato, el texto crudo
marcado ``result_parse_error``.
"""

from __future__ import annotations

import pytest

from inaki.agents.delegation.result_contract import (
    RESULT_FORMAT_FOOTER,
    ResultadoRendido,
    con_footer,
    render_pregunta_no_reanudable,
    render_resultado,
)

_BLOQUE_OK = """\
Armé el informe completo arriba.

```json
{"status": "success", "summary": "Investigué X", "details": "El detalle íntegro", "reason": null}
```"""


class TestConFooter:
    def test_agrega_el_footer_al_final_del_prompt_base(self) -> None:
        assert con_footer("Sos un investigador.") == (
            "Sos un investigador.\n\n" + RESULT_FORMAT_FOOTER
        )

    def test_el_footer_exige_el_bloque_json_con_los_cuatro_campos(self) -> None:
        for campo in ('"status"', '"summary"', '"details"', '"reason"'):
            assert campo in RESULT_FORMAT_FOOTER
        assert "MUST end your response with a fenced JSON block" in RESULT_FORMAT_FOOTER

    def test_con_prompt_por_defecto_cuando_el_caller_no_pasa_uno(self) -> None:
        """REQ-BGD-5 S3: ``system_prompt=None`` del caller → el footer se agrega
        igual, sobre el prompt por defecto del hijo."""
        por_defecto = "Prompt base del sub-agente."

        efectivo = con_footer(None, por_defecto=por_defecto)

        assert efectivo.startswith(por_defecto + "\n\n")
        assert efectivo.endswith(RESULT_FORMAT_FOOTER)

    def test_el_prompt_del_caller_pisa_al_por_defecto_verbatim(self) -> None:
        efectivo = con_footer("Prompt del caller", por_defecto="Prompt base")

        assert efectivo == "Prompt del caller\n\n" + RESULT_FORMAT_FOOTER
        assert "Prompt base" not in efectivo

    def test_un_prompt_vacio_del_caller_es_un_prompt_y_no_cae_al_por_defecto(self) -> None:
        """Misma semántica que REQ-OS-2 / el path sync: solo ``None`` cae al default."""
        efectivo = con_footer("", por_defecto="Prompt base")

        assert "Prompt base" not in efectivo


class TestRenderResultado:
    def test_exito_entrega_resumen_y_detalle_estructurados(self) -> None:
        r = render_resultado("bg-1000", _BLOQUE_OK)

        assert r == ResultadoRendido(
            content="[bg-1000] success: Investigué X\n\nEl detalle íntegro",
            result_status="success",
            parse_error=False,
        )

    def test_exito_sin_detalle_ni_prosa_no_deja_cola_vacia(self) -> None:
        raw = '```json\n{"status": "success", "summary": "Hecho", "details": null}\n```'

        r = render_resultado("bg-1001", raw)

        assert r.content == "[bg-1001] success: Hecho"
        assert r.result_status == "success"

    def test_fallo_declarado_por_el_hijo_lleva_su_razon(self) -> None:
        raw = (
            '```json\n{"status": "failed", "summary": "No pude entrar", '
            '"details": "login rechazado", "reason": "auth_error"}\n```'
        )

        r = render_resultado("bg-1002", raw)

        assert r.content == (
            "[bg-1002] failed (reported by the child, reason: auth_error): "
            "No pude entrar\n\nlogin rechazado"
        )
        assert r.result_status == "failed"
        assert r.parse_error is False

    def test_fallo_declarado_sin_razon_no_inventa_una(self) -> None:
        raw = '```json\n{"status": "failed", "summary": "No pude", "reason": null}\n```'

        r = render_resultado("bg-1003", raw)

        assert r.content == "[bg-1003] failed (reported by the child): No pude"

    def test_sin_bloque_json_entrega_el_texto_crudo_marcado(self) -> None:
        """REQ-BGD-5 S2: prosa sin JSON → el padre recibe el crudo, no se pierde nada."""
        r = render_resultado("bg-1004", "Listo, ya lo hice todo.")

        assert r.parse_error is True
        assert r.result_status is None
        assert r.content == (
            "[bg-1004] completed, but without the required JSON result block "
            "(result_parse_error). Its raw answer follows:\n\nListo, ya lo hice todo."
        )

    def test_json_invalido_tambien_es_parse_error_y_conserva_el_crudo(self) -> None:
        raw = 'Intenté:\n```json\n{"status": "success", "summary": \n```'

        r = render_resultado("bg-1005", raw)

        assert r.parse_error is True
        assert r.content.endswith(raw)


# ---------------------------------------------------------------------------
# delegation-resume — footer pausable y render de la pausa
# ---------------------------------------------------------------------------

# El footer del camino sync NO cambia con este cambio (REQ-RES-1 S2): se fija verbatim para
# que la plantilla única parametrizada no lo altere por accidente.
_FOOTER_SYNC_ORIGINAL = """\
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

_BLOQUE_PAUSA = """\
Avancé hasta donde pude.

```json
{"status": "needs_input", "summary": "Necesito un dato", "details": "Ya leí los 3 informes",
 "reason": null, "question": "¿Cuál de las dos cuentas uso?"}
```"""


class TestFooterPausable:
    def test_el_footer_sync_conserva_su_texto_exacto(self) -> None:
        assert RESULT_FORMAT_FOOTER == _FOOTER_SYNC_ORIGINAL

    def test_footer_sin_pausa_no_menciona_needs_input_ni_question(self) -> None:
        """REQ-RES-1 S2: el camino sync no ofrece la pausa."""
        for footer in (RESULT_FORMAT_FOOTER, con_footer("Base")):
            assert "needs_input" not in footer
            assert "question" not in footer

    def test_footer_pausable_ofrece_needs_input_y_question(self) -> None:
        efectivo = con_footer("Base", puede_pausar=True)

        assert '"success" | "failed" | "needs_input"' in efectivo
        assert '"question"' in efectivo
        assert "required with `needs_input`" in efectivo

    def test_footer_pausable_trae_la_regla_de_una_sola_pregunta_precisa(self) -> None:
        efectivo = con_footer("Base", puede_pausar=True)

        assert "ONE precise question" in efectivo
        assert "Never use it to ask permission" in efectivo
        assert "only the user or the delegating agent can provide" in efectivo

    def test_footer_pausable_conserva_los_cuatro_campos_y_el_cierre(self) -> None:
        efectivo = con_footer("Base", puede_pausar=True)

        for campo in ('"status"', '"summary"', '"details"', '"reason"'):
            assert campo in efectivo
        assert "MUST end your response with a fenced JSON block" in efectivo
        assert efectivo.endswith(
            "This JSON block is the LAST thing in your response. "
            "Do not add any text after the closing fence."
        )

    def test_la_variante_pausable_respeta_el_prompt_base_y_el_por_defecto(self) -> None:
        assert con_footer("Base", puede_pausar=True).startswith("Base\n\n")
        assert con_footer(None, por_defecto="Def", puede_pausar=True).startswith("Def\n\n")
        assert "Def" not in con_footer("Base", por_defecto="Def", puede_pausar=True)

    def test_puede_pausar_es_falso_por_defecto(self) -> None:
        assert con_footer("Base") == con_footer("Base", puede_pausar=False)
        assert con_footer("Base") != con_footer("Base", puede_pausar=True)


class TestRenderPausa:
    def test_needs_input_con_pregunta_pausa_y_expone_la_pregunta(self) -> None:
        r = render_resultado("bg-1010", _BLOQUE_PAUSA, puede_pausar=True)

        assert r.result_status == "needs_input"
        assert r.parse_error is False
        assert r.question == "¿Cuál de las dos cuentas uso?"
        assert r.content.startswith("[bg-1010] needs_input: ¿Cuál de las dos cuentas uso?")

    def test_el_render_ofrece_las_dos_vias_para_continuar(self) -> None:
        """REQ-RES-15: resume propio o preguntar al usuario, y nunca re-delegar."""
        r = render_resultado("bg-1010", _BLOQUE_PAUSA, puede_pausar=True)

        assert 'delegate(resume="bg-1010", task="<answer>")' in r.content
        assert "ask the user" in r.content
        assert "Do NOT start a new delegation" in r.content

    def test_el_render_conserva_los_detalles_del_hijo(self) -> None:
        r = render_resultado("bg-1010", _BLOQUE_PAUSA, puede_pausar=True)

        assert "Ya leí los 3 informes" in r.content
        assert r.details == "Ya leí los 3 informes"

    def test_incluye_la_ruta_del_workdir_cuando_hay(self) -> None:
        """REQ-RES-15 S1."""
        r = render_resultado("bg-1010", _BLOQUE_PAUSA, puede_pausar=True, workdir="dwss/x7k2p9qa/")

        assert "dwss/x7k2p9qa/" in r.content

    def test_sin_workdir_no_menciona_ningun_directorio(self) -> None:
        r = render_resultado("bg-1010", _BLOQUE_PAUSA, puede_pausar=True)

        assert "dwss" not in r.content
        assert "Working directory" not in r.content

    def test_la_pregunta_se_entrega_sin_espacios_sobrantes(self) -> None:
        raw = (
            '```json\n{"status": "needs_input", "summary": "s", "details": null, '
            '"question": "  ¿Sí o no?  \\n"}\n```'
        )

        r = render_resultado("bg-1011", raw, puede_pausar=True)

        assert r.question == "¿Sí o no?"
        assert r.content.startswith("[bg-1011] needs_input: ¿Sí o no?")

    @pytest.mark.parametrize("question", ['""', '"   "', '"\\n\\t "', "null"])
    def test_needs_input_sin_pregunta_o_en_blanco_es_result_parse_error(
        self, question: str
    ) -> None:
        """REQ-RES-1 S3: sin question no hay pausa; el trabajo del hijo no se pierde."""
        raw = (
            '```json\n{"status": "needs_input", "summary": "Necesito algo", '
            f'"details": null, "question": {question}}}\n```'
        )

        r = render_resultado("bg-1012", raw, puede_pausar=True)

        assert r.parse_error is True
        assert r.result_status is None
        assert r.question is None
        assert "result_parse_error" in r.content
        assert raw in r.content

    def test_needs_input_sin_el_campo_question_tambien_es_parse_error(self) -> None:
        raw = '```json\n{"status": "needs_input", "summary": "Necesito algo"}\n```'

        r = render_resultado("bg-1013", raw, puede_pausar=True)

        assert r.parse_error is True
        assert r.question is None

    def test_exito_y_fallo_no_traen_pregunta_ni_details_de_pausa(self) -> None:
        ok = render_resultado("bg-1014", _BLOQUE_OK, puede_pausar=True)

        assert ok.question is None
        assert ok.result_status == "success"
        assert ok.content == "[bg-1014] success: Investigué X\n\nEl detalle íntegro"

    def test_json_valido_y_prosa_sin_json_siguen_igual_con_puede_pausar(self) -> None:
        """REQ-BGD-5 S1-S3: pausable no altera los desenlaces de siempre."""
        assert render_resultado("bg-1015", _BLOQUE_OK, puede_pausar=True) == render_resultado(
            "bg-1015", _BLOQUE_OK
        )
        sin_json = "Listo, ya lo hice todo."
        assert render_resultado("bg-1016", sin_json, puede_pausar=True) == render_resultado(
            "bg-1016", sin_json
        )

    def test_sin_puede_pausar_un_needs_input_no_pausa(self) -> None:
        """El camino que no ofrece la pausa jamás produce una pregunta reanudable."""
        r = render_resultado("bg-1017", _BLOQUE_PAUSA)

        assert r.question is None


class TestRenderPreguntaNoReanudable:
    def _rendido(self) -> ResultadoRendido:
        return render_resultado("bg-1020", _BLOQUE_PAUSA, puede_pausar=True)

    def test_dice_que_no_es_reanudable_y_nombra_el_motivo(self) -> None:
        texto = render_pregunta_no_reanudable("bg-1020", self._rendido(), "snapshot failed")

        assert texto.startswith("[bg-1020] needs_input (NOT resumable: snapshot failed): ")
        assert "¿Cuál de las dos cuentas uso?" in texto

    def test_sugiere_una_nueva_delegacion_con_la_respuesta_incluida(self) -> None:
        """REQ-RES-3 S1/S4."""
        texto = render_pregunta_no_reanudable("bg-1020", self._rendido(), "motivo")

        assert "cannot be resumed" in texto
        assert "NEW delegation" in texto
        assert "includes the answer" in texto

    def test_nunca_promete_una_continuacion(self) -> None:
        texto = render_pregunta_no_reanudable("bg-1020", self._rendido(), "motivo")

        assert "delegate(resume" not in texto
        assert "will continue" not in texto
        assert "keeps all its work" not in texto

    def test_conserva_los_detalles_del_hijo_para_el_contexto(self) -> None:
        texto = render_pregunta_no_reanudable("bg-1020", self._rendido(), "motivo")

        assert "Ya leí los 3 informes" in texto

    def test_sin_detalles_no_deja_cola_vacia(self) -> None:
        raw = '```json\n{"status": "needs_input", "summary": "s", "question": "¿Q?"}\n```'
        rendido = render_resultado("bg-1021", raw, puede_pausar=True)

        texto = render_pregunta_no_reanudable("bg-1021", rendido, "m")

        assert "None" not in texto
        assert texto.startswith("[bg-1021] needs_input (NOT resumable: m): ¿Q?")
