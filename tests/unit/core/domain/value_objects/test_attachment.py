"""Tests de la gramática canónica de attachments (attachment.py).

La gramática es la fuente ÚNICA del formato que los canales persisten en
history.db — estos tests fijan el contrato textual que el LLM aprende vía
ATTACHMENTS_SECTION.
"""

from __future__ import annotations

from core.domain.value_objects.attachment import (
    IncomingAttachment,
    format_album,
    format_attachment,
)


# ---------------------------------------------------------------------------
# header_line
# ---------------------------------------------------------------------------


def test_photo_solo_path():
    att = IncomingAttachment(type="photo", path="/ws/telegram/abc.jpg")
    assert att.header_line() == "@photo at /ws/telegram/abc.jpg"


def test_file_con_nombre_y_mime():
    att = IncomingAttachment(
        type="file",
        name="informe.pdf",
        mime="application/pdf",
        path="/ws/telegram/xyz.pdf",
    )
    assert att.header_line() == "@file informe.pdf (application/pdf) at /ws/telegram/xyz.pdf"


def test_audio_degradado_sin_path_usa_pending_con_id():
    att = IncomingAttachment(type="audio", mime="audio/ogg", file_ref="AUD-uniq")
    line = att.header_line()
    assert line.startswith("@audio (audio/ogg) pending (id: AUD-uniq)")
    assert "download_from_telegram" in line


def test_degradado_sin_file_ref_omite_el_id():
    att = IncomingAttachment(type="video")
    line = att.header_line()
    assert "pending — retrieve with download_from_telegram" in line
    assert "(id:" not in line


# ---------------------------------------------------------------------------
# format_attachment — líneas auxiliares
# ---------------------------------------------------------------------------


def test_orden_fijo_de_auxiliares_transcription_analysis_caption():
    att = IncomingAttachment(type="audio", path="/a.ogg")
    block = format_attachment(
        att, transcription="hola", analysis="una escena", caption="procesá esto"
    )
    lines = block.split("\n")
    assert lines[0] == "@audio at /a.ogg"
    assert lines[1] == "@transcription: hola"
    assert lines[2] == "@analysis: una escena"
    assert lines[3] == "@caption: procesá esto"


def test_auxiliares_vacios_o_blancos_se_omiten():
    att = IncomingAttachment(type="photo", path="/p.jpg")
    block = format_attachment(att, transcription="", analysis="   ", caption=None)
    assert block == "@photo at /p.jpg"


def test_caption_se_strippea():
    att = IncomingAttachment(type="photo", path="/p.jpg")
    block = format_attachment(att, caption="  mirá esto  ")
    assert block.endswith("@caption: mirá esto")


# ---------------------------------------------------------------------------
# format_album
# ---------------------------------------------------------------------------


def test_album_con_miembros_y_caption():
    members = [
        IncomingAttachment(type="photo", path="/1.jpg"),
        IncomingAttachment(type="photo", path="/2.jpg"),
        IncomingAttachment(type="file", name="doc.pdf", mime="application/pdf", path="/3.pdf"),
    ]
    block = format_album(members, caption="guardalas en /fotos")
    lines = block.split("\n")
    assert lines[0] == "@album (3 items):"
    assert lines[1] == "@photo at /1.jpg"
    assert lines[2] == "@photo at /2.jpg"
    assert lines[3] == "@file doc.pdf (application/pdf) at /3.pdf"
    assert lines[4] == "@caption: guardalas en /fotos"


def test_album_vacio_degrada_a_pending():
    block = format_album([], caption="mandalas por mail")
    lines = block.split("\n")
    assert lines[0] == "@album pending — retrieve with download_from_telegram"
    assert lines[1] == "@caption: mandalas por mail"


def test_album_miembro_degradado_conserva_pending():
    members = [
        IncomingAttachment(type="photo", path="/1.jpg"),
        IncomingAttachment(type="photo", file_ref="ph-2"),
    ]
    block = format_album(members)
    assert "@photo at /1.jpg" in block
    assert "@photo pending (id: ph-2)" in block
