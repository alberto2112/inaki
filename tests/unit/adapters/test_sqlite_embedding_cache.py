"""Tests para SqliteEmbeddingCache."""

from pathlib import Path

from adapters.outbound.embedding.sqlite_embedding_cache import SqliteEmbeddingCache


async def test_get_miss_retorna_none(tmp_path: Path):
    cache = SqliteEmbeddingCache(str(tmp_path / "cache.db"))
    resultado = await cache.get("hash_inexistente", "e5_onnx", 384)
    assert resultado is None


async def test_put_y_get_hit(tmp_path: Path):
    cache = SqliteEmbeddingCache(str(tmp_path / "cache.db"))
    embedding = [0.1, 0.2, 0.3]
    await cache.put("abc123", "e5_onnx", 384, embedding)
    resultado = await cache.get("abc123", "e5_onnx", 384)
    assert resultado == embedding


async def test_pk_compuesta_provider_distinto(tmp_path: Path):
    cache = SqliteEmbeddingCache(str(tmp_path / "cache.db"))
    emb_e5 = [0.1, 0.2]
    emb_openai = [0.3, 0.4, 0.5]
    await cache.put("mismo_hash", "e5_onnx", 384, emb_e5)
    await cache.put("mismo_hash", "openai", 1536, emb_openai)

    assert await cache.get("mismo_hash", "e5_onnx", 384) == emb_e5
    assert await cache.get("mismo_hash", "openai", 1536) == emb_openai


async def test_pk_compuesta_dimension_distinta(tmp_path: Path):
    cache = SqliteEmbeddingCache(str(tmp_path / "cache.db"))
    emb_384 = [0.1] * 384
    emb_768 = [0.2] * 768
    await cache.put("mismo_hash", "e5_onnx", 384, emb_384)
    await cache.put("mismo_hash", "e5_onnx", 768, emb_768)

    assert await cache.get("mismo_hash", "e5_onnx", 384) == emb_384
    assert await cache.get("mismo_hash", "e5_onnx", 768) == emb_768


async def test_insert_or_replace_sobreescribe(tmp_path: Path):
    cache = SqliteEmbeddingCache(str(tmp_path / "cache.db"))
    await cache.put("hash1", "e5_onnx", 384, [1.0, 2.0])
    await cache.put("hash1", "e5_onnx", 384, [3.0, 4.0])
    resultado = await cache.get("hash1", "e5_onnx", 384)
    assert resultado == [3.0, 4.0]


async def test_crea_directorio_y_schema_automaticamente(tmp_path: Path):
    db_path = tmp_path / "sub" / "dir" / "cache.db"
    cache = SqliteEmbeddingCache(str(db_path))
    await cache.put("hash1", "e5_onnx", 384, [0.1])
    assert db_path.exists()
    resultado = await cache.get("hash1", "e5_onnx", 384)
    assert resultado == [0.1]
