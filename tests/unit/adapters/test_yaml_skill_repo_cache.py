"""Tests para integración de YamlSkillRepository con IEmbeddingCache."""

from pathlib import Path
from unittest.mock import AsyncMock

from adapters.outbound.skills.yaml_skill_repo import YamlSkillRepository


def _write_skill_yaml(path: Path, name: str = "test_skill", desc: str = "desc") -> Path:
    skill_file = path / f"{name}.yaml"
    skill_file.write_text(
        f"id: {name}\nname: {name}\ndescription: {desc}\ntags:\n  - test\n",
        encoding="utf-8",
    )
    return skill_file


async def test_cache_miss_llama_embed_y_put(tmp_path: Path):
    embedder = AsyncMock()
    embedder.embed_passage.return_value = [0.1] * 384
    cache = AsyncMock()
    cache.get.return_value = None  # miss

    repo = YamlSkillRepository(embedder, cache=cache, dimension=384)
    repo.add_file(_write_skill_yaml(tmp_path))
    await repo.list_all()

    embedder.embed_passage.assert_called_once()
    cache.put.assert_called_once()


async def test_cache_hit_no_llama_embed(tmp_path: Path):
    embedder = AsyncMock()
    embedder.embed_passage.return_value = [0.1] * 384
    cached_embedding = [0.5] * 384
    cache = AsyncMock()
    cache.get.return_value = cached_embedding  # hit

    repo = YamlSkillRepository(embedder, cache=cache, dimension=384)
    repo.add_file(_write_skill_yaml(tmp_path))
    skills = await repo.list_all()

    embedder.embed_passage.assert_not_called()
    cache.put.assert_not_called()
    assert len(skills) == 1
    assert repo._embeddings[0] == cached_embedding


async def test_archivo_modificado_genera_miss(tmp_path: Path):
    embedder = AsyncMock()
    embedder.embed_passage.return_value = [0.1] * 384
    cache = AsyncMock()
    cache.get.return_value = None  # siempre miss

    skill_file = _write_skill_yaml(tmp_path, desc="version 1")
    repo = YamlSkillRepository(embedder, cache=cache, dimension=384)
    repo.add_file(skill_file)
    await repo.list_all()

    # Modificar el archivo y forzar recarga
    skill_file.write_text(
        "id: test_skill\nname: test_skill\ndescription: version 2\ntags:\n  - test\n",
        encoding="utf-8",
    )
    repo._loaded = False
    await repo.list_all()

    assert embedder.embed_passage.call_count == 2
    # Debe haber llamado get con hashes diferentes
    hashes = [call.args[0] for call in cache.get.call_args_list]
    assert hashes[0] != hashes[1]


async def test_sin_cache_comportamiento_original(tmp_path: Path):
    embedder = AsyncMock()
    embedder.embed_passage.return_value = [0.1] * 384

    repo = YamlSkillRepository(embedder)  # sin cache
    repo.add_file(_write_skill_yaml(tmp_path))
    skills = await repo.list_all()

    embedder.embed_passage.assert_called_once()
    assert len(skills) == 1
