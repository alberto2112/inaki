"""Tests de ``result_contract``: footer de formato y render del resultado async.

REQ-BGD-5 / REQ-DG-11: el hijo async recibe el MISMO footer JSON que el sync y su
respuesta se parsea con ``parse_delegation_result``; el padre recibe el resultado
estructurado como ``[bg-N] ...`` o, si el hijo no respetó el formato, el texto crudo
marcado ``result_parse_error``.
"""

from __future__ import annotations

from inaki.agents.delegation.result_contract import (
    RESULT_FORMAT_FOOTER,
    ResultadoRendido,
    con_footer,
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
