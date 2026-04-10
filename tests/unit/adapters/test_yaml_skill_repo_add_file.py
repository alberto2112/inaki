"""Tests para YamlSkillRepository.add_file()."""

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
async def test_add_file_loads_skill(tmp_path: Path) -> None:
    """add_file con YAML válido → skill aparece en list_all()."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    repo = YamlSkillRepository(str(skills_dir), FakeEmbedder())

    extra = _write_skill(tmp_path / "extra.yaml", "extra_skill")
    repo.add_file(extra)

    skills = await repo.list_all()
    assert any(s.id == "extra_skill" for s in skills)


@pytest.mark.asyncio
async def test_add_file_combines_with_dir(tmp_path: Path) -> None:
    """skills_dir + add_file → ambas fuentes presentes en list_all()."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_skill(skills_dir / "builtin.yaml", "builtin")

    repo = YamlSkillRepository(str(skills_dir), FakeEmbedder())

    extra = _write_skill(tmp_path / "extra.yaml", "extra")
    repo.add_file(extra)

    skills = await repo.list_all()
    ids = {s.id for s in skills}
    assert "builtin" in ids
    assert "extra" in ids


@pytest.mark.asyncio
async def test_add_file_invalidates_cache(tmp_path: Path) -> None:
    """list_all() → add_file() → list_all() da N+1 skills."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_skill(skills_dir / "builtin.yaml", "builtin")

    repo = YamlSkillRepository(str(skills_dir), FakeEmbedder())

    before = await repo.list_all()
    assert len(before) == 1

    extra = _write_skill(tmp_path / "extra.yaml", "extra")
    repo.add_file(extra)

    after = await repo.list_all()
    assert len(after) == 2


@pytest.mark.asyncio
async def test_add_file_deduplicates(tmp_path: Path) -> None:
    """Misma path dos veces → una sola entrada en list_all()."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    repo = YamlSkillRepository(str(skills_dir), FakeEmbedder())

    extra = _write_skill(tmp_path / "extra.yaml", "extra")
    repo.add_file(extra)
    repo.add_file(extra)  # duplicado

    skills = await repo.list_all()
    assert len([s for s in skills if s.id == "extra"]) == 1


@pytest.mark.asyncio
async def test_add_file_deduplicates_with_dir(tmp_path: Path) -> None:
    """Extra ya bajo skills_dir → no se carga dos veces."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    builtin = _write_skill(skills_dir / "builtin.yaml", "builtin")

    repo = YamlSkillRepository(str(skills_dir), FakeEmbedder())

    # Añadir la misma skill que ya está en el dir
    repo.add_file(builtin)

    skills = await repo.list_all()
    assert len([s for s in skills if s.id == "builtin"]) == 1


@pytest.mark.asyncio
async def test_add_file_missing_path_no_crash(tmp_path: Path) -> None:
    """Path inexistente → warning sin lanzar excepción, list_all() funciona."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    repo = YamlSkillRepository(str(skills_dir), FakeEmbedder())
    repo.add_file(tmp_path / "nonexistent.yaml")

    skills = await repo.list_all()
    assert isinstance(skills, list)
