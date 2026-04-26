"""
Tests unitarios para la lógica de enmascaramiento de MaskedInput.

Los tests de la función pura ``_mascara`` cubren todos los boundary cases
según UX-decision#2 (masking <12).

Los tests de widget headless se omiten aquí — están documentados como
candidatos para el smoke test manual en Phase 8.
"""

from __future__ import annotations


from adapters.inbound.setup_tui.widgets.masked_input import _mascara, _BULLETS


# ---------------------------------------------------------------------------
# Tests de la función pura _mascara — todos los boundary cases
# ---------------------------------------------------------------------------


class TestMascara:
    """Prueba la función de cálculo de texto enmascarado."""

    def test_vacio_retorna_string_vacio(self) -> None:
        assert _mascara("") == ""

    def test_un_caracter_retorna_bullets_fijos(self) -> None:
        resultado = _mascara("x")
        assert resultado == _BULLETS
        assert len(resultado) == 8

    def test_once_caracteres_retorna_bullets_fijos(self) -> None:
        # 11 < 12 → bullets fijos
        resultado = _mascara("x" * 11)
        assert resultado == _BULLETS

    def test_doce_caracteres_usa_formato_largo(self) -> None:
        # 12 >= 12 → formato XXXXX•••YYYY
        valor = "abcde" + "m" * 3 + "wxyz"  # 12 chars
        resultado = _mascara(valor)
        assert resultado.startswith("abcde")
        assert resultado.endswith("wxyz")
        # Tiene bullets en el medio
        assert "•" in resultado

    def test_doce_caracteres_boundary_primeros_y_ultimos(self) -> None:
        valor = "12345" + "0" * 3 + "6789"  # 12 chars: "12345" + "000" + "6789"
        resultado = _mascara(valor)
        assert resultado[:5] == "12345"
        assert resultado[-4:] == "6789"

    def test_cadena_larga_expone_primeros_cinco_y_ultimos_cuatro(self) -> None:
        valor = "AAAAA" + "x" * 20 + "ZZZZ"  # 29 chars
        resultado = _mascara(valor)
        assert resultado.startswith("AAAAA")
        assert resultado.endswith("ZZZZ")

    def test_exactamente_once_boundary_inferior(self) -> None:
        # 11 es el último que usa bullets fijos
        resultado = _mascara("a" * 11)
        assert resultado == _BULLETS

    def test_doce_es_primer_valor_con_formato_largo(self) -> None:
        # 12 es el primer valor que usa el formato XXXXX•••YYYY
        resultado = _mascara("a" * 12)
        assert resultado != _BULLETS
        assert len(resultado) > 8

    def test_secreto_tipico_token_telegram(self) -> None:
        # Los tokens de Telegram son ~46 chars: 1234567890:ABCDEFGHIJKlm...
        token = "1234567890:ABCDEFGHIJKLmnopqrstuvwxyz1234567"
        resultado = _mascara(token)
        assert resultado.startswith("12345")
        # Los últimos 4 del token
        assert resultado.endswith(token[-4:])

    def test_exactamente_cero_boundary(self) -> None:
        assert _mascara("") == ""

    def test_dos_caracteres_usa_bullets_fijos(self) -> None:
        assert _mascara("ab") == _BULLETS

    def test_bullets_fijos_son_ocho(self) -> None:
        assert len(_BULLETS) == 8


# ---------------------------------------------------------------------------
# Tests de la lógica de display integrada
# ---------------------------------------------------------------------------


class TestMascaraFormatoDetallado:
    """Verifica el formato exacto del enmascaramiento largo."""

    def test_formato_largo_tiene_bullets_en_medio(self) -> None:
        valor = "123456789012"  # 12 chars
        resultado = _mascara(valor)
        # Chars 0-4: "12345", chars -4: "9012"
        assert resultado[0:5] == "12345"
        assert resultado[-4:] == "9012"
        parte_media = resultado[5:-4]
        assert all(c == "•" for c in parte_media)

    def test_formato_largo_no_expone_chars_del_medio(self) -> None:
        # El medio es puro bullets — no filtra información
        valor = "INICIO_SECRETO_FINAL"  # 20 chars
        resultado = _mascara(valor)
        # La parte media no debe contener "SECRETO"
        assert "SECRETO" not in resultado
        assert resultado.startswith("INICI")
        assert resultado.endswith("INAL")
