"""Test de integración: round-trip completo con SqliteEmbeddingCache real."""

from pathlib import Path

from adapters.outbound.embedding.sqlite_embedding_cache import SqliteEmbeddingCache


async def test_put_get_round_trip(tmp_path: Path):
    cache = SqliteEmbeddingCache(str(tmp_path / "cache.db"))
    embedding = [0.1 * i for i in range(384)]

    await cache.put("hash1", "e5_onnx", 384, embedding)
    resultado = await cache.get("hash1", "e5_onnx", 384)

    assert resultado is not None
    assert len(resultado) == 384
    assert abs(resultado[0] - 0.0) < 1e-6
    assert abs(resultado[1] - 0.1) < 1e-6


async def test_clave_inexistente_retorna_none(tmp_path: Path):
    cache = SqliteEmbeddingCache(str(tmp_path / "cache.db"))
    assert await cache.get("no_existe", "e5_onnx", 384) is None


async def test_pk_compuesta_independencia(tmp_path: Path):
    cache = SqliteEmbeddingCache(str(tmp_path / "cache.db"))
    emb_a = [1.0, 2.0]
    emb_b = [3.0, 4.0]

    await cache.put("hash1", "e5_onnx", 384, emb_a)
    await cache.put("hash1", "openai", 1536, emb_b)

    assert await cache.get("hash1", "e5_onnx", 384) == emb_a
    assert await cache.get("hash1", "openai", 1536) == emb_b
    assert await cache.get("hash1", "e5_onnx", 1536) is None


async def test_ciclo_miss_hit_invalidacion(tmp_path: Path):
    """Simula: primer arranque (miss) → segundo arranque (hit) → cambio (miss)."""
    db_path = str(tmp_path / "cache.db")
    embedding_v1 = [0.1] * 384
    embedding_v2 = [0.9] * 384

    # Primer arranque: miss → put
    cache1 = SqliteEmbeddingCache(db_path)
    assert await cache1.get("hash_v1", "e5_onnx", 384) is None
    await cache1.put("hash_v1", "e5_onnx", 384, embedding_v1)

    # Segundo arranque (nueva instancia, misma DB): hit
    cache2 = SqliteEmbeddingCache(db_path)
    resultado = await cache2.get("hash_v1", "e5_onnx", 384)
    assert resultado == embedding_v1

    # Archivo modificado (hash diferente): miss → put
    assert await cache2.get("hash_v2", "e5_onnx", 384) is None
    await cache2.put("hash_v2", "e5_onnx", 384, embedding_v2)
    assert await cache2.get("hash_v2", "e5_onnx", 384) == embedding_v2
