"""Tests para YamlSkillRepository.add_file() — única fuente de skills."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from adapters.outbound.skills.yaml_skill_repo import YamlSkillRepository


# ---------------------------------------------------------------------------
# Fake embedder
# ---------------------------------------------------------------------------

class FakeEmbedder:
    async def embed_passage(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    async def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_skill(path: Path, skill_id: str) -> Path:
    path.write_text(
        textwrap.dedent(f"""\
            id: "{skill_id}"
            name: "Skill {skill_id}"
            description: "Desc {skill_id}"
            instructions: "Do {skill_id}"
            tags:
              - "{skill_id}"
        """),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_repo_returns_no_skills() -> None:
    """Repo sin ningún add_file → list_all() devuelve lista vacía."""
    repo = YamlSkillRepository(FakeEmbedder())
    skills = await repo.list_all()
    assert skills == []


@pytest.mark.asyncio
async def test_add_file_loads_skill(tmp_path: Path) -> None:
    """add_file con YAML válido → skill aparece en list_all()."""
    repo = YamlSkillRepository(FakeEmbedder())

    extra = _write_skill(tmp_path / "extra.yaml", "extra_skill")
    repo.add_file(extra)

    skills = await repo.list_all()
    assert any(s.id == "extra_skill" for s in skills)


@pytest.mark.asyncio
async def test_add_file_multiple_sources(tmp_path: Path) -> None:
    """add_file con N archivos → todos presentes en list_all()."""
    repo = YamlSkillRepository(FakeEmbedder())

    repo.add_file(_write_skill(tmp_path / "a.yaml", "alpha"))
    repo.add_file(_write_skill(tmp_path / "b.yaml", "beta"))

    skills = await repo.list_all()
    ids = {s.id for s in skills}
    assert "alpha" in ids
    assert "beta" in ids


@pytest.mark.asyncio
async def test_add_file_invalidates_cache(tmp_path: Path) -> None:
    """list_all() → add_file() → list_all() refleja la skill nueva."""
    repo = YamlSkillRepository(FakeEmbedder())

    repo.add_file(_write_skill(tmp_path / "a.yaml", "alpha"))
    before = await repo.list_all()
    assert len(before) == 1

    repo.add_file(_write_skill(tmp_path / "b.yaml", "beta"))
    after = await repo.list_all()
    assert len(after) == 2


@pytest.mark.asyncio
async def test_add_file_deduplicates(tmp_path: Path) -> None:
    """Mismo path dos veces → una sola entrada en list_all()."""
    repo = YamlSkillRepository(FakeEmbedder())

    extra = _write_skill(tmp_path / "extra.yaml", "extra")
    repo.add_file(extra)
    repo.add_file(extra)  # duplicado

    skills = await repo.list_all()
    assert len([s for s in skills if s.id == "extra"]) == 1


@pytest.mark.asyncio
async def test_add_file_missing_path_no_crash(tmp_path: Path) -> None:
    """Path inexistente → warning sin lanzar excepción, list_all() funciona."""
    repo = YamlSkillRepository(FakeEmbedder())
    repo.add_file(tmp_path / "nonexistent.yaml")

    skills = await repo.list_all()
    assert isinstance(skills, list)
