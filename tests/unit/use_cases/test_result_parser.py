"""Tests unitarios para core/use_cases/_result_parser.py — parse_delegation_result."""

from __future__ import annotations

import json


from core.domain.value_objects.delegation_result import DelegationResult
from core.use_cases._result_parser import parse_delegation_result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _json_block(data: dict) -> str:
    """Envuelve un dict en un bloque ```json ... ```."""
    return f"```json\n{json.dumps(data)}\n```"


_VALID_RESULT = {
    "status": "success",
    "summary": "Tarea completada exitosamente.",
}

_VALID_RESULT_FULL = {
    "status": "success",
    "summary": "Tarea completada.",
    "details": "Detalles de la ejecución.",
    "reason": None,
}


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_single_valid_block():
    """Texto con un único bloque ```json``` válido → DelegationResult parseado."""
    text = f"Hice la tarea.\n\n{_json_block(_VALID_RESULT)}"

    result = parse_delegation_result(text)

    assert isinstance(result, DelegationResult)
    assert result.status == "success"
    assert result.summary == "Tarea completada exitosamente."
    assert result.details is None
    assert result.reason is None


def test_happy_path_all_fields_present():
    """Todos los campos del DelegationResult presentes y correctamente mapeados."""
    data = {
        "status": "success",
        "summary": "Resumen.",
        "details": "Detalles.",
        "reason": None,
    }
    text = _json_block(data)

    result = parse_delegation_result(text)

    assert result.status == "success"
    assert result.summary == "Resumen."
    assert result.details == "Detalles."
    assert result.reason is None


def test_happy_path_block_not_at_end_of_line():
    """El bloque json puede estar en cualquier posición del texto."""
    text = "Intro de texto.\n\n" + _json_block(_VALID_RESULT) + "\n\nFin del mensaje."

    result = parse_delegation_result(text)

    assert result.status == "success"


def test_happy_path_failed_status():
    """status='failed' con reason es válido también."""
    data = {
        "status": "failed",
        "summary": "No pude completar la tarea.",
        "reason": "child_exception:ValueError",
    }
    text = _json_block(data)

    result = parse_delegation_result(text)

    assert result.status == "failed"
    assert result.reason == "child_exception:ValueError"


# ---------------------------------------------------------------------------
# No json block
# ---------------------------------------------------------------------------


def test_no_json_block_returns_parse_error():
    """Sin bloque ```json``` → DelegationResult con status=failed, reason=result_parse_error."""
    text = "Respuesta sin ningún bloque json fenced."

    result = parse_delegation_result(text)

    assert result.status == "failed"
    assert result.reason == "result_parse_error"
    assert result.details == text


def test_empty_string_returns_parse_error():
    """Texto vacío → result_parse_error."""
    result = parse_delegation_result("")

    assert result.status == "failed"
    assert result.reason == "result_parse_error"


def test_python_block_not_json_block():
    """Un bloque ```python``` no cuenta como json block."""
    text = "```python\nprint('hello')\n```"

    result = parse_delegation_result(text)

    assert result.status == "failed"
    assert result.reason == "result_parse_error"


def test_unmarked_code_block_not_json_block():
    """Un bloque ``` sin lenguaje no cuenta."""
    text = "```\n{\"status\": \"success\", \"summary\": \"ok\"}\n```"

    result = parse_delegation_result(text)

    assert result.status == "failed"
    assert result.reason == "result_parse_error"


# ---------------------------------------------------------------------------
# Invalid JSON inside the block
# ---------------------------------------------------------------------------


def test_invalid_json_syntax_returns_parse_error():
    """Bloque ```json``` con JSON inválido → result_parse_error."""
    text = "```json\n{ invalid json here }\n```"

    result = parse_delegation_result(text)

    assert result.status == "failed"
    assert result.reason == "result_parse_error"
    assert result.details == text


def test_json_array_not_dict_returns_parse_error():
    """Un bloque json con un array en lugar de dict → ValidationError → result_parse_error."""
    text = "```json\n[1, 2, 3]\n```"

    result = parse_delegation_result(text)

    assert result.status == "failed"
    assert result.reason == "result_parse_error"


# ---------------------------------------------------------------------------
# Missing required fields
# ---------------------------------------------------------------------------


def test_missing_status_field_returns_parse_error():
    """JSON sin campo 'status' → ValidationError → result_parse_error."""
    data = {"summary": "ok"}
    text = _json_block(data)

    result = parse_delegation_result(text)

    assert result.status == "failed"
    assert result.reason == "result_parse_error"


def test_missing_summary_field_returns_parse_error():
    """JSON sin campo 'summary' → ValidationError → result_parse_error."""
    data = {"status": "success"}
    text = _json_block(data)

    result = parse_delegation_result(text)

    assert result.status == "failed"
    assert result.reason == "result_parse_error"


def test_missing_both_required_fields_returns_parse_error():
    """JSON vacío {} → faltan ambos campos requeridos → result_parse_error."""
    text = _json_block({})

    result = parse_delegation_result(text)

    assert result.status == "failed"
    assert result.reason == "result_parse_error"


# ---------------------------------------------------------------------------
# Last-wins rule — multiple blocks
# ---------------------------------------------------------------------------


def test_last_block_wins_when_multiple_json_blocks():
    """Con múltiples bloques ```json```, el ÚLTIMO es el que se parsea."""
    first_block = _json_block({"status": "failed", "summary": "Primer intento."})
    last_block = _json_block({"status": "success", "summary": "Resultado final."})

    text = f"Texto inicial.\n\n{first_block}\n\nAlgo más.\n\n{last_block}"

    result = parse_delegation_result(text)

    assert result.status == "success"
    assert result.summary == "Resultado final."


def test_last_block_wins_even_if_first_is_invalid():
    """El último bloque válido gana, aunque el primero sea inválido."""
    first_block = "```json\n{ invalid }\n```"
    last_block = _json_block(_VALID_RESULT)

    text = f"{first_block}\n\n{last_block}"

    result = parse_delegation_result(text)

    assert result.status == "success"


def test_last_block_is_invalid_returns_parse_error_even_if_first_valid():
    """Si el ÚLTIMO bloque es inválido, devuelve error — aunque haya uno válido antes."""
    first_block = _json_block(_VALID_RESULT)
    last_block = "```json\n{ broken }\n```"

    text = f"{first_block}\n\nAlgo de texto.\n\n{last_block}"

    result = parse_delegation_result(text)

    assert result.status == "failed"
    assert result.reason == "result_parse_error"


def test_three_blocks_last_one_parsed():
    """Con tres bloques json, solo el tercero (último) se parsea."""
    data_1 = {"status": "failed", "summary": "uno"}
    data_2 = {"status": "failed", "summary": "dos"}
    data_3 = {"status": "success", "summary": "tres"}

    text = (
        f"{_json_block(data_1)}\n\n"
        f"{_json_block(data_2)}\n\n"
        f"{_json_block(data_3)}"
    )

    result = parse_delegation_result(text)

    assert result.summary == "tres"


def test_text_after_last_block_does_not_affect_extraction():
    """El texto DESPUÉS del último bloque no impide que sea el último bloque extraído."""
    data = {"status": "success", "summary": "ok"}
    text = f"Prefix.\n\n{_json_block(data)}\n\nEste texto es posterior al bloque."

    result = parse_delegation_result(text)

    # El bloque sigue siendo el último json block aunque haya texto después
    assert result.status == "success"
    assert result.summary == "ok"


# ---------------------------------------------------------------------------
# Never raises guarantee
# ---------------------------------------------------------------------------


def test_never_raises_on_arbitrary_text():
    """parse_delegation_result nunca debe lanzar excepción bajo ninguna entrada."""
    inputs = [
        "",
        "   ",
        "texto plano sin bloques",
        "```json\n```",  # bloque vacío
        "```json\nnull\n```",  # null en vez de dict
        "```json\n\"string\"\n```",  # string en vez de dict
        "```json\n{ sin cierre",  # JSON sin cerrar
    ]
    for text in inputs:
        result = parse_delegation_result(text)
        assert isinstance(result, DelegationResult)
        assert result.status == "failed"
        assert result.reason == "result_parse_error"
