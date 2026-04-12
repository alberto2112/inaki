"""Tests para filtrado por min_score en YamlSkillRepository.retrieve()."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

from adapters.outbound.skills.yaml_skill_repo import YamlSkillRepository


def _write_skill_yaml(path: Path, name: str) -> Path:
    skill_file = path / f"{name}.yaml"
    skill_file.write_text(
        f"id: {name}\nname: {name}\ndescription: skill {name}\ntags:\n  - test\n",
        encoding="utf-8",
    )
    return skill_file


async def test_min_score_filtra_skills_por_debajo_del_umbral(tmp_path: Path):
    embedder = AsyncMock()
    embedder.embed_passage.return_value = [1.0]

    repo = YamlSkillRepository(embedder)
    for name in ("alta", "media", "baja"):
        repo.add_file(_write_skill_yaml(tmp_path, name))
    await repo.list_all()

    scores = [0.9, 0.5, 0.1]  # mismo orden que _skills
    call_count = 0

    def fake_cosine(q, emb):
        nonlocal call_count
        idx = call_count
        call_count += 1
        return scores[idx]

    with patch(
        "adapters.outbound.skills.yaml_skill_repo.cosine_similarity",
        side_effect=fake_cosine,
    ):
        result = await repo.retrieve([1.0], top_k=10, min_score=0.4)

    nombres = {s.name for s in result}
    assert "alta" in nombres
    assert "media" in nombres
    assert "baja" not in nombres


async def test_min_score_cero_no_filtra(tmp_path: Path):
    embedder = AsyncMock()
    embedder.embed_passage.return_value = [1.0]

    repo = YamlSkillRepository(embedder)
    for name in ("a", "b"):
        repo.add_file(_write_skill_yaml(tmp_path, name))
    await repo.list_all()

    with patch(
        "adapters.outbound.skills.yaml_skill_repo.cosine_similarity",
        return_value=0.05,
    ):
        result = await repo.retrieve([1.0], top_k=10, min_score=0.0)

    assert len(result) == 2


async def test_min_score_combina_con_top_k(tmp_path: Path):
    embedder = AsyncMock()
    embedder.embed_passage.return_value = [1.0]

    repo = YamlSkillRepository(embedder)
    for name in ("a", "b", "c"):
        repo.add_file(_write_skill_yaml(tmp_path, name))
    await repo.list_all()

    with patch(
        "adapters.outbound.skills.yaml_skill_repo.cosine_similarity",
        return_value=0.8,
    ):
        result = await repo.retrieve([1.0], top_k=1, min_score=0.5)

    # Las 3 pasan min_score=0.5, pero top_k=1 limita a 1
    assert len(result) == 1


async def test_min_score_alto_devuelve_vacio(tmp_path: Path):
    embedder = AsyncMock()
    embedder.embed_passage.return_value = [1.0]

    repo = YamlSkillRepository(embedder)
    repo.add_file(_write_skill_yaml(tmp_path, "a"))
    await repo.list_all()

    with patch(
        "adapters.outbound.skills.yaml_skill_repo.cosine_similarity",
        return_value=0.3,
    ):
        result = await repo.retrieve([1.0], top_k=10, min_score=0.9)

    assert result == []
