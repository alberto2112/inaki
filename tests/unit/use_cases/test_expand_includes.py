"""Tests unitarios para ``expand_includes`` (``core/use_cases/_turn_pipeline.py``).

Cubre la directiva ``@include(<archivo>)`` que se expande en el ``system_prompt`` del
operador y en el contexto per-user, ANTES de la sustitución de variables ``{{...}}``.

Reglas verificadas:
  - Include limpio (solo en su línea, tolera indentación) → reemplazo por contenido.
  - Path absoluto vs relativo (relativo se ancla contra ``base_dir`` = home).
  - Profundidad 1: un ``@include`` dentro de un archivo incluido NO se expande.
  - Archivo faltante / error de lectura → se suprime la línea entera (+ warning).
  - Include sucio (no aislado en su línea) → se borra solo el token, sobrevive la prosa.
  - Sin directivas → texto intacto (no-op).
"""

from __future__ import annotations

import logging

from core.use_cases._turn_pipeline import expand_includes


def test_sin_directiva_es_no_op():
    texto = "Línea uno\nLínea dos sin includes."
    assert expand_includes(texto, "/cualquier/base") == texto


def test_include_relativo_se_ancla_contra_base_dir(tmp_path):
    (tmp_path / "intro.md").write_text("CONTENIDO INTRO", encoding="utf-8")
    texto = "Antes\n@include(intro.md)\nDespués"
    assert expand_includes(texto, str(tmp_path)) == "Antes\nCONTENIDO INTRO\nDespués"


def test_include_absoluto_ignora_base_dir(tmp_path):
    archivo = tmp_path / "abs.md"
    archivo.write_text("ABSOLUTO", encoding="utf-8")
    texto = f"@include({archivo})"
    # base_dir distinto: el path absoluto no lo usa.
    assert expand_includes(texto, "/otra/base") == "ABSOLUTO"


def test_tolera_espacios_de_indentacion(tmp_path):
    (tmp_path / "x.md").write_text("OK", encoding="utf-8")
    assert expand_includes("    @include(x.md)", str(tmp_path)) == "OK"
    assert expand_includes("@include(  x.md  )", str(tmp_path)) == "OK"  # espacios internos


def test_profundidad_uno_no_reexpande(tmp_path):
    # El archivo incluido trae a su vez un @include: NO debe expandirse.
    (tmp_path / "nivel1.md").write_text("L1\n@include(nivel2.md)", encoding="utf-8")
    (tmp_path / "nivel2.md").write_text("NO_DEBERIA_APARECER", encoding="utf-8")
    resultado = expand_includes("@include(nivel1.md)", str(tmp_path))
    assert resultado == "L1\n@include(nivel2.md)"
    assert "NO_DEBERIA_APARECER" not in resultado


def test_archivo_faltante_suprime_la_linea(tmp_path, caplog):
    texto = "Antes\n@include(no_existe.md)\nDespués"
    with caplog.at_level(logging.WARNING):
        resultado = expand_includes(texto, str(tmp_path))
    assert resultado == "Antes\nDespués"
    assert "no resuelto" in caplog.text


def test_include_sucio_borra_solo_el_token(tmp_path, caplog):
    # El archivo existe, pero el include no está aislado en su línea → no se expande,
    # se limpia el token y sobrevive la prosa.
    (tmp_path / "mi_archivo.md").write_text("NO_USAR", encoding="utf-8")
    texto = "Lorem ipsum dolor @include(mi_archivo.md)"
    with caplog.at_level(logging.WARNING):
        resultado = expand_includes(texto, str(tmp_path))
    assert resultado == "Lorem ipsum dolor "
    assert "NO_USAR" not in resultado
    assert "sucio" in caplog.text


def test_variables_dentro_del_include_quedan_para_resolver_despues(tmp_path):
    # expand_includes corre ANTES de _resolve_vars: deja los {{...}} intactos.
    (tmp_path / "fecha.md").write_text("Hoy es {{DATE}}", encoding="utf-8")
    assert expand_includes("@include(fecha.md)", str(tmp_path)) == "Hoy es {{DATE}}"


def test_multiples_includes_en_un_texto(tmp_path):
    (tmp_path / "a.md").write_text("AAA", encoding="utf-8")
    (tmp_path / "b.md").write_text("BBB", encoding="utf-8")
    texto = "@include(a.md)\nmedio\n@include(b.md)"
    assert expand_includes(texto, str(tmp_path)) == "AAA\nmedio\nBBB"
