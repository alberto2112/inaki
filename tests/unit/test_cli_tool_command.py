"""Tests para el comando `tool` en inaki/cli.py.

Cubre:
  - --list happy path (default)
  - --list --verbose (incluye descripción y schema)
  - invocación con --arg k=v (parseo de tipos)
  - invocación con --json
  - invocación sin args
  - --raw flag
  - error de validación: --list + nombre mutuamente excluyentes
  - error de validación: --arg + --json mutuamente excluyentes
  - error de validación: sin --list ni nombre
  - tool falla (success=false) → exit 1, error en stderr
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_client(
    tools: list[dict] | None = None,
    invoke_result: dict | None = None,
) -> MagicMock:
    """Construye un DaemonClient mock listo para los tests de `tool`."""
    client = MagicMock()
    client.health.return_value = True
    client.list_tools.return_value = {
        "tools": tools
        or [
            {
                "name": "shell_exec",
                "description": "Ejecuta un comando shell",
                "parameters_schema": {"type": "object", "properties": {"cmd": {"type": "string"}}},
            },
            {
                "name": "read_file",
                "description": "Lee un archivo",
                "parameters_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
            },
        ]
    }
    client.invoke_tool.return_value = invoke_result or {
        "tool_name": "shell_exec",
        "output": '{"exitcode": 0, "stdout": "ok"}',
        "success": True,
        "error": None,
    }
    return client


def _invoke_tool(args: list[str], mock_client: MagicMock | None = None):
    """Helper: invoca el CLI con args dados y devuelve el result de CliRunner."""
    from inaki.cli import app

    runner = CliRunner()
    client = mock_client or _make_mock_client()
    mock_global_config = MagicMock()
    mock_global_config.app.default_agent = "dev"

    with patch("inaki.cli._build_daemon_client", return_value=(client, mock_global_config)):
        result = runner.invoke(app, ["tool"] + args)
    return result, client


# ---------------------------------------------------------------------------
# --list happy path
# ---------------------------------------------------------------------------


def test_tool_list_muestra_nombres() -> None:
    """--list sin --verbose imprime solo los nombres de las tools, uno por línea."""
    result, _ = _invoke_tool(["--list"])

    assert result.exit_code == 0, result.output
    assert "shell_exec" in result.output
    assert "read_file" in result.output


def test_tool_list_verbose_muestra_descripcion() -> None:
    """--list --verbose muestra descripción de cada tool."""
    result, _ = _invoke_tool(["--list", "--verbose"])

    assert result.exit_code == 0, result.output
    assert "Ejecuta un comando shell" in result.output
    assert "Lee un archivo" in result.output


def test_tool_list_verbose_muestra_schema() -> None:
    """--list --verbose incluye el schema de parámetros."""
    result, _ = _invoke_tool(["--list", "--verbose"])

    assert result.exit_code == 0, result.output
    # El schema contiene el tipo "object" al menos
    assert "object" in result.output


# ---------------------------------------------------------------------------
# Invocación con --arg
# ---------------------------------------------------------------------------


def test_tool_invocacion_arg_string() -> None:
    """--arg key=valor pasa el valor como string cuando no parsea como JSON."""
    result, client = _invoke_tool(["shell_exec", "--arg", "cmd=ls -la"])

    assert result.exit_code == 0, result.output
    args, kwargs = client.invoke_tool.call_args
    args_pasados = args[2] if len(args) >= 3 else kwargs.get("args", {})
    assert args_pasados["cmd"] == "ls -la"


def test_tool_invocacion_arg_entero() -> None:
    """--arg n=5 parsea como int vía JSON."""
    result, client = _invoke_tool(["shell_exec", "--arg", "n=5"])

    assert result.exit_code == 0, result.output
    args, _ = client.invoke_tool.call_args
    args_pasados = args[2]
    assert args_pasados["n"] == 5
    assert isinstance(args_pasados["n"], int)


def test_tool_invocacion_arg_bool() -> None:
    """--arg flag=true parsea como bool True."""
    result, client = _invoke_tool(["shell_exec", "--arg", "flag=true"])

    assert result.exit_code == 0, result.output
    args, _ = client.invoke_tool.call_args
    args_pasados = args[2]
    assert args_pasados["flag"] is True


def test_tool_invocacion_arg_null() -> None:
    """--arg x=null parsea como None."""
    result, client = _invoke_tool(["shell_exec", "--arg", "x=null"])

    assert result.exit_code == 0, result.output
    args, _ = client.invoke_tool.call_args
    args_pasados = args[2]
    assert args_pasados["x"] is None


def test_tool_invocacion_arg_multiple() -> None:
    """Múltiples --arg construyen el dict correctamente."""
    result, client = _invoke_tool(["shell_exec", "--arg", "a=1", "--arg", "b=dos"])

    assert result.exit_code == 0, result.output
    args, _ = client.invoke_tool.call_args
    args_pasados = args[2]
    assert args_pasados["a"] == 1
    assert args_pasados["b"] == "dos"


def test_tool_invocacion_arg_sin_igual_es_error() -> None:
    """Un --arg sin '=' es error de validación (exit 2)."""
    result, _ = _invoke_tool(["shell_exec", "--arg", "malformado"])

    assert result.exit_code == 2


def test_tool_invocacion_arg_valor_vacio() -> None:
    """--arg k= (valor vacío) pasa string vacío."""
    result, client = _invoke_tool(["shell_exec", "--arg", "k="])

    assert result.exit_code == 0, result.output
    args, _ = client.invoke_tool.call_args
    args_pasados = args[2]
    assert args_pasados["k"] == ""


# ---------------------------------------------------------------------------
# Invocación con --json
# ---------------------------------------------------------------------------


def test_tool_invocacion_json() -> None:
    """--json '{\"n\": 5}' pasa el dict parseado."""
    result, client = _invoke_tool(["shell_exec", "--json", '{"n": 5, "s": "hola"}'])

    assert result.exit_code == 0, result.output
    args, _ = client.invoke_tool.call_args
    args_pasados = args[2]
    assert args_pasados["n"] == 5
    assert args_pasados["s"] == "hola"


def test_tool_invocacion_json_invalido_es_error() -> None:
    """--json con JSON inválido es exit 2."""
    result, _ = _invoke_tool(["shell_exec", "--json", "no-es-json"])

    assert result.exit_code == 2


def test_tool_invocacion_json_no_objeto_es_error() -> None:
    """--json con array en vez de object es exit 2."""
    result, _ = _invoke_tool(["shell_exec", "--json", "[1,2,3]"])

    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# Invocación sin args
# ---------------------------------------------------------------------------


def test_tool_invocacion_sin_args() -> None:
    """Invocación sin --arg ni --json pasa dict vacío."""
    result, client = _invoke_tool(["shell_exec"])

    assert result.exit_code == 0, result.output
    args, _ = client.invoke_tool.call_args
    args_pasados = args[2]
    assert args_pasados == {}


# ---------------------------------------------------------------------------
# --raw flag
# ---------------------------------------------------------------------------


def test_tool_raw_imprime_output_crudo() -> None:
    """--raw imprime el output literal sin formatear como JSON."""
    crudo = '{"exitcode":0,"stdout":"ok"}'
    client = _make_mock_client(
        invoke_result={"tool_name": "shell_exec", "output": crudo, "success": True, "error": None}
    )
    result, _ = _invoke_tool(["shell_exec", "--raw"], mock_client=client)

    assert result.exit_code == 0, result.output
    assert crudo in result.output


def test_tool_sin_raw_formatea_json() -> None:
    """Sin --raw, si el output es JSON, lo imprime indentado."""
    crudo = '{"exitcode":0,"stdout":"ok"}'
    client = _make_mock_client(
        invoke_result={"tool_name": "shell_exec", "output": crudo, "success": True, "error": None}
    )
    result, _ = _invoke_tool(["shell_exec"], mock_client=client)

    assert result.exit_code == 0, result.output
    # Debe estar indentado (tiene saltos de línea adicionales)
    datos = json.loads(crudo)
    esperado = json.dumps(datos, indent=2, ensure_ascii=False)
    assert esperado in result.output


def test_tool_sin_raw_output_no_json_imprime_crudo() -> None:
    """Sin --raw, si el output no es JSON, lo imprime tal cual."""
    client = _make_mock_client(
        invoke_result={
            "tool_name": "shell_exec",
            "output": "texto libre",
            "success": True,
            "error": None,
        }
    )
    result, _ = _invoke_tool(["shell_exec"], mock_client=client)

    assert result.exit_code == 0, result.output
    assert "texto libre" in result.output


# ---------------------------------------------------------------------------
# Errores de validación de uso
# ---------------------------------------------------------------------------


def test_tool_list_y_nombre_a_la_vez_es_error() -> None:
    """--list + nombre son mutuamente excluyentes → exit 2."""
    result, _ = _invoke_tool(["--list", "shell_exec"])

    assert result.exit_code == 2


def test_tool_arg_y_json_a_la_vez_es_error() -> None:
    """--arg y --json son mutuamente excluyentes → exit 2."""
    result, _ = _invoke_tool(["shell_exec", "--arg", "k=v", "--json", '{"k": "v"}'])

    assert result.exit_code == 2


def test_tool_sin_list_ni_nombre_es_error() -> None:
    """Sin --list ni nombre de tool → exit 2."""
    result, _ = _invoke_tool([])

    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# Tool falla (success=false)
# ---------------------------------------------------------------------------


def test_tool_falla_sale_con_exit_1() -> None:
    """Cuando success=False, el comando sale con exit code 1."""
    client = _make_mock_client(
        invoke_result={
            "tool_name": "inexistente",
            "output": "",
            "success": False,
            "error": "tool no registrada",
        }
    )
    result, _ = _invoke_tool(["inexistente"], mock_client=client)

    assert result.exit_code == 1


def test_tool_falla_imprime_mensaje_de_error() -> None:
    """Cuando success=False, el mensaje de error aparece en el output."""
    client = _make_mock_client(
        invoke_result={
            "tool_name": "inexistente",
            "output": "",
            "success": False,
            "error": "tool no registrada",
        }
    )
    result, _ = _invoke_tool(["inexistente"], mock_client=client)

    assert result.exit_code == 1
    assert "tool no registrada" in result.output
