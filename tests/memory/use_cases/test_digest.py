"""Tests unitarios para DigestWriter — renderer único del digest markdown."""

from datetime import datetime, timezone
from pathlib import Path

from inaki.kernel.domain.agent_settings import MemorySettings
from inaki.kernel.domain.memory import MemoryEntry
from inaki.memory.use_cases.digest import DigestWriter


def _make_entry(content: str, tags: list[str], created_at: datetime) -> MemoryEntry:
    return MemoryEntry(
        content=content,
        embedding=[0.1] * 384,
        relevance=0.9,
        tags=tags,
        created_at=created_at,
    )


def test_render_incluye_cabecera_nueva_y_una_linea_por_recuerdo(mock_memory):
    cfg = MemorySettings(digest_size=3)
    writer = DigestWriter(mock_memory, agent_id="test", settings=cfg)

    entries = [
        _make_entry(
            "Le gusta Python", ["tech", "python"], datetime(2026, 4, 9, tzinfo=timezone.utc)
        ),
        _make_entry("Usa LazyVim", [], datetime(2026, 4, 8, tzinfo=timezone.utc)),
    ]

    markdown = writer.render(entries)

    assert markdown.startswith("# Recuerdos sobre el usuario")
    assert "<!-- Generado por inaki —" in markdown
    lines = markdown.splitlines()
    # Orden preservado tal como llega — el writer no reordena.
    assert lines[3] == "- [2026-04-09] Le gusta Python (tech, python)"
    assert lines[4] == "- [2026-04-08] Usa LazyVim"


def test_render_sin_recuerdos_produce_solo_cabecera(mock_memory):
    cfg = MemorySettings(digest_size=3)
    writer = DigestWriter(mock_memory, agent_id="test", settings=cfg)

    markdown = writer.render([])

    assert markdown.startswith("# Recuerdos sobre el usuario")
    assert markdown.endswith("\n")
    # Solo cabecera + línea en blanco, ninguna entrada.
    assert "- [" not in markdown


async def test_write_crea_directorios_y_escribe_el_scope_correcto(mock_memory, tmp_path):
    template = tmp_path / "mem" / "digest_{channel}_{chat_id}.md"
    cfg = MemorySettings(digest_size=5, digest_template=str(template))
    entry = _make_entry("Le gusta Python", ["tech"], datetime(2026, 4, 9, tzinfo=timezone.utc))
    mock_memory.get_recent.return_value = [entry]
    writer = DigestWriter(mock_memory, agent_id="test", settings=cfg)

    assert not (tmp_path / "mem").exists()

    await writer.write("telegram", "-1001")

    expected_path = cfg.resolved_digest_path("telegram", "-1001")
    assert expected_path.exists()
    content = expected_path.read_text(encoding="utf-8")
    assert "Le gusta Python" in content


async def test_write_respeta_digest_size_y_filtra_por_agente_y_scope(mock_memory, tmp_path):
    template = tmp_path / "mem" / "digest_{channel}_{chat_id}.md"
    cfg = MemorySettings(digest_size=7, digest_template=str(template))
    mock_memory.get_recent.return_value = []
    writer = DigestWriter(mock_memory, agent_id="agente-x", settings=cfg)

    await writer.write("cli", None)

    mock_memory.get_recent.assert_called_once_with(
        7,
        agent_id="agente-x",
        channel="cli",
        chat_id=None,
    )


async def test_write_no_propaga_si_get_recent_lanza(mock_memory, tmp_path):
    template = tmp_path / "mem" / "digest_{channel}_{chat_id}.md"
    cfg = MemorySettings(digest_size=3, digest_template=str(template))
    mock_memory.get_recent.side_effect = RuntimeError("DB caída")
    writer = DigestWriter(mock_memory, agent_id="test", settings=cfg)

    # No debe lanzar — best-effort.
    await writer.write("telegram", "-1001")

    assert not cfg.resolved_digest_path("telegram", "-1001").exists()


async def test_write_no_propaga_si_el_path_no_es_escribible(mock_memory, tmp_path, monkeypatch):
    template = tmp_path / "mem" / "digest_{channel}_{chat_id}.md"
    cfg = MemorySettings(digest_size=3, digest_template=str(template))
    mock_memory.get_recent.return_value = []
    writer = DigestWriter(mock_memory, agent_id="test", settings=cfg)

    def _raise_write_text(self, *args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", _raise_write_text)

    # No debe lanzar — best-effort.
    await writer.write("telegram", "-1001")
