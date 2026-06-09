"""Tests para el comando `send` en inaki/cli.py.

Cubre:
  - --text happy path
  - --photo con archivo existente (usa tmp_path)
  - --album con N fotos
  - --caption con media OK
  - --caption con --text rechazado (exit 2)
  - destination malformado → exit 2
  - múltiples flags de contenido (mutex) → exit 2
  - sin flags de contenido → exit 2
  - path no existe → exit 2 (sin --remote)
  - con --remote activo, path no existente NO rechaza localmente (warning + continúa)
  - IDs negativos en chat_id
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_client() -> MagicMock:
    """DaemonClient mock para tests de send."""
    client = MagicMock()
    client.health.return_value = True
    client.send_message_via.return_value = {
        "sent": True,
        "channel": "telegram",
        "chat_id": "4879536",
        "kind": "text",
    }
    return client


def _invoke_send(
    args: list[str],
    mock_client: MagicMock | None = None,
    remote_url: str | None = None,
) -> tuple:
    """Helper: invoca `inaki send` con args dados.

    Si remote_url se pasa, simula el flag --remote inyectando en ctx.obj.
    Retorna (result, client).
    """
    from inaki.cli import app

    runner = CliRunner()
    client = mock_client or _make_mock_client()
    mock_global_config = MagicMock()
    mock_global_config.app.default_agent = "dev"

    full_args = []
    if remote_url:
        full_args += ["--remote", remote_url]
    full_args += ["send"] + args

    with patch("inaki.cli._build_daemon_client", return_value=(client, mock_global_config)):
        result = runner.invoke(app, full_args)
    return result, client


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_send_text_happy() -> None:
    """--text envía con kind=text y exit 0."""
    result, client = _invoke_send(["telegram:4879536", "--text", "Hola mundo"])

    assert result.exit_code == 0, result.output
    assert "enviado" in result.output
    client.send_message_via.assert_called_once()
    _, canal, chat_id, kind = client.send_message_via.call_args[0]
    assert canal == "telegram"
    assert chat_id == "4879536"
    assert kind == "text"
    kwargs = client.send_message_via.call_args[1]
    assert kwargs["text"] == "Hola mundo"


def test_send_photo_archivo_existente(tmp_path: Path) -> None:
    """--photo con archivo existente envía con kind=photo."""
    foto = tmp_path / "foto.jpg"
    foto.write_bytes(b"\xff\xd8\xff")  # bytes mínimos JPEG

    result, client = _invoke_send(["telegram:4879536", "--photo", str(foto)])

    assert result.exit_code == 0, result.output
    _, _, _, kind = client.send_message_via.call_args[0]
    assert kind == "photo"
    kwargs = client.send_message_via.call_args[1]
    assert str(foto) in kwargs["sources"]


def test_send_photo_con_caption(tmp_path: Path) -> None:
    """--photo + --caption envía caption en kwargs."""
    foto = tmp_path / "foto.jpg"
    foto.write_bytes(b"\xff\xd8\xff")

    result, client = _invoke_send(
        ["telegram:4879536", "--photo", str(foto), "--caption", "Mirá esto"]
    )

    assert result.exit_code == 0, result.output
    kwargs = client.send_message_via.call_args[1]
    assert kwargs["caption"] == "Mirá esto"


def test_send_audio(tmp_path: Path) -> None:
    """--audio envía con kind=audio."""
    pista = tmp_path / "audio.mp3"
    pista.write_bytes(b"ID3")

    result, client = _invoke_send(["telegram:4879536", "--audio", str(pista)])

    assert result.exit_code == 0, result.output
    _, _, _, kind = client.send_message_via.call_args[0]
    assert kind == "audio"


def test_send_video(tmp_path: Path) -> None:
    """--video envía con kind=video."""
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00\x00\x00\x18ftyp")

    result, client = _invoke_send(["telegram:4879536", "--video", str(clip)])

    assert result.exit_code == 0, result.output
    _, _, _, kind = client.send_message_via.call_args[0]
    assert kind == "video"


def test_send_file(tmp_path: Path) -> None:
    """--file envía con kind=file."""
    doc = tmp_path / "doc.pdf"
    doc.write_bytes(b"%PDF-1.4")

    result, client = _invoke_send(["telegram:4879536", "--file", str(doc)])

    assert result.exit_code == 0, result.output
    _, _, _, kind = client.send_message_via.call_args[0]
    assert kind == "file"


def test_send_album_multiples_fotos(tmp_path: Path) -> None:
    """--album con N fotos envía kind=album con sources de todos los paths."""
    foto1 = tmp_path / "a.jpg"
    foto2 = tmp_path / "b.jpg"
    foto3 = tmp_path / "c.jpg"
    for f in [foto1, foto2, foto3]:
        f.write_bytes(b"\xff\xd8\xff")

    result, client = _invoke_send(
        [
            "telegram:4879536",
            "--album",
            str(foto1),
            "--album",
            str(foto2),
            "--album",
            str(foto3),
        ]
    )

    assert result.exit_code == 0, result.output
    _, _, _, kind = client.send_message_via.call_args[0]
    assert kind == "album"
    kwargs = client.send_message_via.call_args[1]
    assert len(kwargs["sources"]) == 3


def test_send_album_con_caption(tmp_path: Path) -> None:
    """--album + --caption propaga el caption."""
    foto = tmp_path / "img.jpg"
    foto.write_bytes(b"\xff\xd8\xff")

    result, client = _invoke_send(
        ["telegram:4879536", "--album", str(foto), "--caption", "Mis fotos"]
    )

    assert result.exit_code == 0, result.output
    kwargs = client.send_message_via.call_args[1]
    assert kwargs["caption"] == "Mis fotos"


# ---------------------------------------------------------------------------
# IDs negativos en chat_id
# ---------------------------------------------------------------------------


def test_send_chat_id_negativo() -> None:
    """IDs negativos (grupos de Telegram) deben parsearse correctamente."""
    result, client = _invoke_send(["telegram:-1001234567890", "--text", "Grupo"])

    assert result.exit_code == 0, result.output
    _, _, chat_id, _ = client.send_message_via.call_args[0]
    assert chat_id == "-1001234567890"


def test_send_canal_con_dos_puntos_en_chat_id() -> None:
    """El split se hace por el PRIMER ':' — chat_id puede no tener ':'."""
    result, client = _invoke_send(["telegram:4879536", "--text", "Hola"])

    assert result.exit_code == 0, result.output
    _, canal, chat_id, _ = client.send_message_via.call_args[0]
    assert canal == "telegram"
    assert chat_id == "4879536"


# ---------------------------------------------------------------------------
# Errores de validación de destination
# ---------------------------------------------------------------------------


def test_send_destination_sin_dos_puntos_es_error() -> None:
    """Destination sin ':' → exit 2 con mensaje claro."""
    result, _ = _invoke_send(["telegram4879536", "--text", "Hola"])

    assert result.exit_code == 2


def test_send_destination_canal_vacio_es_error() -> None:
    """Destination con canal vacío ':4879536' → exit 2."""
    result, _ = _invoke_send([":4879536", "--text", "Hola"])

    assert result.exit_code == 2


def test_send_destination_chat_id_vacio_es_error() -> None:
    """Destination con chat_id vacío 'telegram:' → exit 2."""
    result, _ = _invoke_send(["telegram:", "--text", "Hola"])

    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# Errores de validación de flags de contenido
# ---------------------------------------------------------------------------


def test_send_sin_flags_de_contenido_es_error() -> None:
    """Sin ningún flag de contenido → exit 2."""
    result, _ = _invoke_send(["telegram:4879536"])

    assert result.exit_code == 2


def test_send_text_y_photo_a_la_vez_es_error(tmp_path: Path) -> None:
    """--text y --photo son mutuamente excluyentes → exit 2."""
    foto = tmp_path / "foto.jpg"
    foto.write_bytes(b"\xff\xd8\xff")

    result, _ = _invoke_send(["telegram:4879536", "--text", "Hola", "--photo", str(foto)])

    assert result.exit_code == 2


def test_send_caption_con_text_es_error() -> None:
    """--caption no es válido con --text → exit 2."""
    result, _ = _invoke_send(["telegram:4879536", "--text", "Hola", "--caption", "pie de foto"])

    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# Validación de existencia de paths locales
# ---------------------------------------------------------------------------


def test_send_photo_path_inexistente_sin_remote_es_error() -> None:
    """Sin --remote, un path que no existe → exit 2 (feedback inmediato)."""
    result, _ = _invoke_send(
        ["telegram:4879536", "--photo", "/tmp/inaki_test_no_existe_abc123.jpg"]
    )

    assert result.exit_code == 2


def test_send_photo_path_inexistente_con_remote_no_rechaza() -> None:
    """Con --remote, path inexistente NO causa exit 2 (daemon remoto valida)."""
    # El daemon remoto fallará pero el cliente local no debe rechazar
    result, client = _invoke_send(
        ["telegram:4879536", "--photo", "/tmp/inaki_test_no_existe_abc123.jpg"],
        remote_url="http://raspi.local:6497",
    )

    # No debe ser exit 2 por validación local — puede ser 0 (mock acepta la call)
    assert result.exit_code != 2


def test_send_photo_path_inexistente_con_remote_imprime_warning() -> None:
    """Con --remote, se imprime advertencia de que los paths no se validan localmente."""
    runner = CliRunner()
    mock_client = _make_mock_client()
    mock_global_config = MagicMock()
    mock_global_config.app.default_agent = "dev"

    from inaki.cli import app

    with patch("inaki.cli._build_daemon_client", return_value=(mock_client, mock_global_config)):
        result = runner.invoke(
            app,
            [
                "--remote",
                "http://raspi.local:6497",
                "send",
                "telegram:4879536",
                "--photo",
                "/tmp/no_existe.jpg",
            ],
        )

    # La advertencia va al output mezclado (typer mezcla stdout+stderr)
    assert "remoto" in result.output.lower() or "remote" in result.output.lower()


def test_send_album_un_path_inexistente_sin_remote_es_error(tmp_path: Path) -> None:
    """En album sin --remote, si algún path no existe → exit 2."""
    foto_real = tmp_path / "real.jpg"
    foto_real.write_bytes(b"\xff\xd8\xff")

    result, _ = _invoke_send(
        [
            "telegram:4879536",
            "--album",
            str(foto_real),
            "--album",
            "/tmp/inaki_test_no_existe_abc123.jpg",
        ]
    )

    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# Agente custom
# ---------------------------------------------------------------------------


def test_send_agent_custom() -> None:
    """--agent pasa el agent_id correcto al DaemonClient."""
    result, client = _invoke_send(["telegram:4879536", "--text", "Hola", "--agent", "anacleto"])

    assert result.exit_code == 0, result.output
    agente_usado = client.send_message_via.call_args[0][0]
    assert agente_usado == "anacleto"


# ---------------------------------------------------------------------------
# --no-broadcast flag
# ---------------------------------------------------------------------------


def test_send_no_broadcast_manda_broadcast_false() -> None:
    """--no-broadcast pasa broadcast=False en la llamada al DaemonClient."""
    result, client = _invoke_send(["telegram:4879536", "--text", "Script CI", "--no-broadcast"])

    assert result.exit_code == 0, result.output
    kwargs = client.send_message_via.call_args[1]
    assert kwargs["broadcast"] is False


def test_send_sin_no_broadcast_manda_broadcast_true() -> None:
    """Sin --no-broadcast, broadcast=True (default) en la llamada al DaemonClient."""
    result, client = _invoke_send(["telegram:4879536", "--text", "Mensaje normal"])

    assert result.exit_code == 0, result.output
    kwargs = client.send_message_via.call_args[1]
    assert kwargs["broadcast"] is True


def test_send_output_refleja_broadcasted_true() -> None:
    """Si el response trae broadcasted=True, el output del CLI incluye [broadcast]."""
    client = _make_mock_client()
    client.send_message_via.return_value = {
        "sent": True,
        "channel": "telegram",
        "chat_id": "4879536",
        "kind": "text",
        "broadcasted": True,
    }
    result, _ = _invoke_send(["telegram:4879536", "--text", "Hola"], mock_client=client)

    assert result.exit_code == 0, result.output
    assert "[broadcast]" in result.output


def test_send_output_sin_broadcasted_no_muestra_tag() -> None:
    """Si broadcasted=False (o ausente), el output NO incluye [broadcast]."""
    result, _ = _invoke_send(["telegram:4879536", "--text", "Hola"])

    assert result.exit_code == 0, result.output
    assert "[broadcast]" not in result.output
