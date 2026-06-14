"""Tests unitarios para ReconcileMemoryUseCase — protocolo de reconciliación."""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from core.domain.entities.memory import MemoryEntry
from core.domain.value_objects.agent_settings import MemorySettings
from core.use_cases.reconcile_memory import ReconcileMemoryUseCase


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry(
    eid: str,
    content: str,
    *,
    channel: str | None = "telegram",
    chat_id: str | None = "100",
    relevance: float = 0.9,
    reconciled: bool = False,
) -> MemoryEntry:
    """Crea un MemoryEntry mínimo para tests."""
    return MemoryEntry(
        id=eid,
        content=content,
        embedding=[0.1] * 384,
        relevance=relevance,
        tags=[],
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        agent_id="test",
        channel=channel,
        chat_id=chat_id,
        reconciled=reconciled,
    )


def _make_uc(mock_llm, mock_memory, mock_embedder) -> ReconcileMemoryUseCase:
    cfg = MemorySettings(
        reconcile_enabled=True,
        reconcile_similarity_threshold=0.80,
        reconcile_top_k=10,
    )
    return ReconcileMemoryUseCase(
        llm=mock_llm,
        memory=mock_memory,
        embedder=mock_embedder,
        agent_id="test",
        memory_config=cfg,
    )


# ---------------------------------------------------------------------------
# 1. Sin seeds — no-op
# ---------------------------------------------------------------------------


async def test_sin_seeds_retorna_mensaje_sin_operar(mock_llm, mock_memory, mock_embedder):
    mock_memory.load_unreconciled.return_value = []
    uc = _make_uc(mock_llm, mock_memory, mock_embedder)

    result = await uc.execute()

    assert "no hay recuerdos nuevos" in result.lower()
    mock_memory.mark_reconciled.assert_not_called()
    mock_llm.complete.assert_not_called()


# ---------------------------------------------------------------------------
# 2. Seed sin vecinos sobre el umbral → mark_reconciled([seed.id]), sin LLM
# ---------------------------------------------------------------------------


async def test_seed_sin_vecinos_marca_reconciliado_sin_llamar_llm(
    mock_llm, mock_memory, mock_embedder
):
    seed = _entry("seed-1", "me gusta el café")
    mock_memory.load_unreconciled.return_value = [seed]
    # search_with_scores devuelve el propio seed con score alto, pero sin vecinos distintos
    mock_memory.search_with_scores.return_value = [(seed, 0.99)]

    uc = _make_uc(mock_llm, mock_memory, mock_embedder)
    await uc.execute()

    mock_memory.mark_reconciled.assert_called_once_with(["seed-1"])
    mock_llm.complete.assert_not_called()


async def test_seed_sin_vecinos_sobre_umbral_marca_reconciliado(
    mock_llm, mock_memory, mock_embedder
):
    seed = _entry("seed-1", "me gusta el café")
    vecino = _entry("vecino-1", "algo no relacionado")
    mock_memory.load_unreconciled.return_value = [seed]
    # score < 0.80 → no entra al cluster
    mock_memory.search_with_scores.return_value = [(seed, 0.99), (vecino, 0.5)]

    uc = _make_uc(mock_llm, mock_memory, mock_embedder)
    await uc.execute()

    mock_memory.mark_reconciled.assert_called_once_with(["seed-1"])
    mock_llm.complete.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Acción merge
# ---------------------------------------------------------------------------


async def test_merge_crea_nuevo_entry_con_reconciled_true(
    mock_llm, mock_memory, mock_embedder
):
    seed = _entry("seed-1", "estoy enfermo con gripe")
    vecino = _entry("vecino-1", "ya me recuperé de la gripe")
    mock_memory.load_unreconciled.return_value = [seed]
    mock_memory.search_with_scores.return_value = [(seed, 0.99), (vecino, 0.92)]
    mock_memory.delete.return_value = vecino  # simula borrado exitoso

    accion = {
        "action": "merge",
        "target_ids": ["vecino-1"],
        "new_content": "Tuvo gripe en enero; recuperado en febrero, ya sin tratamiento",
        "new_relevance": 0.9,
        "new_tags": ["salud"],
    }
    from core.domain.value_objects.llm_response import LLMResponse

    mock_llm.complete.return_value = LLMResponse.of_text(json.dumps([accion]))
    mock_embedder.embed_passage.return_value = [0.2] * 384

    uc = _make_uc(mock_llm, mock_memory, mock_embedder)
    await uc.execute()

    # Debe haber llamado embed_passage con el nuevo contenido
    mock_embedder.embed_passage.assert_called_once_with(
        "Tuvo gripe en enero; recuperado en febrero, ya sin tratamiento"
    )

    # El nuevo MemoryEntry se persiste con reconciled=True (anti-loop)
    mock_memory.store.assert_called_once()
    nuevo = mock_memory.store.call_args.args[0]
    assert nuevo.reconciled is True
    assert nuevo.content == "Tuvo gripe en enero; recuperado en febrero, ya sin tratamiento"
    assert nuevo.relevance == 0.9
    assert nuevo.tags == ["salud"]
    assert nuevo.agent_id == "test"
    assert nuevo.channel == "telegram"
    assert nuevo.chat_id == "100"

    # Los target_ids se soft-deletean
    mock_memory.delete.assert_called_once_with("vecino-1")


async def test_merge_incluye_target_en_ids_borrados_no_reconciliados(
    mock_llm, mock_memory, mock_embedder
):
    """El id borrado por merge no debe aparecer en mark_reconciled."""
    seed = _entry("seed-1", "estoy enfermo")
    vecino = _entry("vecino-1", "me recuperé")
    mock_memory.load_unreconciled.return_value = [seed]
    mock_memory.search_with_scores.return_value = [(seed, 0.99), (vecino, 0.90)]
    mock_memory.delete.return_value = vecino

    accion = {
        "action": "merge",
        "target_ids": ["vecino-1"],
        "new_content": "Tuvo gripe y se recuperó",
        "new_relevance": 0.85,
        "new_tags": [],
    }
    from core.domain.value_objects.llm_response import LLMResponse

    mock_llm.complete.return_value = LLMResponse.of_text(json.dumps([accion]))
    mock_embedder.embed_passage.return_value = [0.2] * 384

    uc = _make_uc(mock_llm, mock_memory, mock_embedder)
    await uc.execute()

    # mark_reconciled debe incluir seed-1 (no borrado) pero NO vecino-1 (borrado)
    calls = mock_memory.mark_reconciled.call_args_list
    ids_reconciliados = [arg for call in calls for arg in call.args[0]]
    assert "seed-1" in ids_reconciliados
    assert "vecino-1" not in ids_reconciliados


# ---------------------------------------------------------------------------
# 4. Acción supersede
# ---------------------------------------------------------------------------


async def test_supersede_borra_target_ids(mock_llm, mock_memory, mock_embedder):
    seed = _entry("seed-1", "tomo ibuprofeno")
    vecino = _entry("vecino-1", "ya no tomo ibuprofeno")
    mock_memory.load_unreconciled.return_value = [seed]
    mock_memory.search_with_scores.return_value = [(seed, 0.99), (vecino, 0.88)]
    mock_memory.delete.return_value = vecino

    accion = {"action": "supersede", "target_ids": ["vecino-1"]}
    from core.domain.value_objects.llm_response import LLMResponse

    mock_llm.complete.return_value = LLMResponse.of_text(json.dumps([accion]))

    uc = _make_uc(mock_llm, mock_memory, mock_embedder)
    await uc.execute()

    mock_memory.delete.assert_called_once_with("vecino-1")
    mock_memory.store.assert_not_called()


# ---------------------------------------------------------------------------
# 5. Acción downweight
# ---------------------------------------------------------------------------


async def test_downweight_actualiza_relevance(mock_llm, mock_memory, mock_embedder):
    seed = _entry("seed-1", "algo nuevo importante")
    vecino = _entry("vecino-1", "algo viejo quizás obsoleto", relevance=0.8)
    mock_memory.load_unreconciled.return_value = [seed]
    mock_memory.search_with_scores.return_value = [(seed, 0.99), (vecino, 0.85)]
    mock_memory.update.return_value = vecino

    accion = {"action": "downweight", "target_ids": ["vecino-1"], "new_relevance": 0.2}
    from core.domain.value_objects.llm_response import LLMResponse

    mock_llm.complete.return_value = LLMResponse.of_text(json.dumps([accion]))

    uc = _make_uc(mock_llm, mock_memory, mock_embedder)
    await uc.execute()

    mock_memory.update.assert_called_once_with("vecino-1", relevance=0.2)
    mock_memory.delete.assert_not_called()
    mock_memory.store.assert_not_called()


# ---------------------------------------------------------------------------
# 6. Acción keep → no borra ni crea, pero marca reconciliado
# ---------------------------------------------------------------------------


async def test_keep_no_modifica_entries_pero_marca_reconciliados(
    mock_llm, mock_memory, mock_embedder
):
    seed = _entry("seed-1", "prefiero café")
    vecino = _entry("vecino-1", "siempre toma café")
    mock_memory.load_unreconciled.return_value = [seed]
    mock_memory.search_with_scores.return_value = [(seed, 0.99), (vecino, 0.91)]

    from core.domain.value_objects.llm_response import LLMResponse

    mock_llm.complete.return_value = LLMResponse.of_text(json.dumps([{"action": "keep"}]))

    uc = _make_uc(mock_llm, mock_memory, mock_embedder)
    await uc.execute()

    mock_memory.delete.assert_not_called()
    mock_memory.store.assert_not_called()
    mock_memory.update.assert_not_called()

    # Ambos ids deben quedar marcados como reconciliados
    calls = mock_memory.mark_reconciled.call_args_list
    ids_reconciliados = [arg for call in calls for arg in call.args[0]]
    assert "seed-1" in ids_reconciliados
    assert "vecino-1" in ids_reconciliados


# ---------------------------------------------------------------------------
# 7. Filtrado por scope — vecino de otro scope no entra al cluster
# ---------------------------------------------------------------------------


async def test_vecino_de_otro_scope_no_entra_al_cluster(
    mock_llm, mock_memory, mock_embedder
):
    seed = _entry("seed-1", "algo", channel="telegram", chat_id="100")
    vecino_mismo = _entry("vec-ok", "similar", channel="telegram", chat_id="100")
    vecino_otro_canal = _entry("vec-canal", "similar", channel="cli", chat_id="100")
    vecino_otro_chat = _entry("vec-chat", "similar", channel="telegram", chat_id="999")
    vecino_sin_scope = _entry("vec-none", "similar", channel=None, chat_id=None)

    mock_memory.load_unreconciled.return_value = [seed]
    mock_memory.search_with_scores.return_value = [
        (seed, 0.99),
        (vecino_mismo, 0.90),
        (vecino_otro_canal, 0.95),  # score alto pero scope distinto
        (vecino_otro_chat, 0.93),  # scope distinto
        (vecino_sin_scope, 0.91),  # scope distinto (None != "telegram")
    ]

    from core.domain.value_objects.llm_response import LLMResponse

    # LLM solo debe recibir seed-1 y vec-ok
    mock_llm.complete.return_value = LLMResponse.of_text("[]")

    uc = _make_uc(mock_llm, mock_memory, mock_embedder)
    await uc.execute()

    # El LLM se llamó (hay vecino en el mismo scope)
    mock_llm.complete.assert_called_once()
    # Verificar que el task enviado al LLM solo contiene los ids del scope correcto
    call_args = mock_llm.complete.call_args
    messages = call_args.kwargs.get("messages") or call_args.args[0]
    task_content = messages[0].content
    assert "seed-1" in task_content
    assert "vec-ok" in task_content
    assert "vec-canal" not in task_content
    assert "vec-chat" not in task_content
    assert "vec-none" not in task_content


# ---------------------------------------------------------------------------
# 8. Vecino por debajo del umbral no entra al cluster
# ---------------------------------------------------------------------------


async def test_vecino_bajo_umbral_no_entra_al_cluster(
    mock_llm, mock_memory, mock_embedder
):
    seed = _entry("seed-1", "algo")
    vecino_bajo = _entry("vec-bajo", "similar", relevance=0.5)
    mock_memory.load_unreconciled.return_value = [seed]
    # score=0.79 < threshold=0.80 → excluido
    mock_memory.search_with_scores.return_value = [(seed, 0.99), (vecino_bajo, 0.79)]

    uc = _make_uc(mock_llm, mock_memory, mock_embedder)
    await uc.execute()

    # Sin vecinos → no llama al LLM
    mock_llm.complete.assert_not_called()
    mock_memory.mark_reconciled.assert_called_once_with(["seed-1"])


# ---------------------------------------------------------------------------
# 9. Anti-loop: el MemoryEntry creado por merge tiene reconciled=True
# ---------------------------------------------------------------------------


async def test_nuevo_entry_de_merge_tiene_reconciled_true(
    mock_llm, mock_memory, mock_embedder
):
    """Garantía explícita del anti-loop: el nuevo recuerdo no re-entra como seed."""
    seed = _entry("s1", "contenido A")
    vecino = _entry("v1", "contenido B")
    mock_memory.load_unreconciled.return_value = [seed]
    mock_memory.search_with_scores.return_value = [(seed, 0.99), (vecino, 0.90)]
    mock_memory.delete.return_value = vecino

    accion = {
        "action": "merge",
        "target_ids": ["v1"],
        "new_content": "contenido fusionado",
        "new_relevance": 0.85,
        "new_tags": [],
    }
    from core.domain.value_objects.llm_response import LLMResponse

    mock_llm.complete.return_value = LLMResponse.of_text(json.dumps([accion]))
    mock_embedder.embed_passage.return_value = [0.2] * 384

    uc = _make_uc(mock_llm, mock_memory, mock_embedder)
    await uc.execute()

    stored = mock_memory.store.call_args.args[0]
    assert stored.reconciled is True


# ---------------------------------------------------------------------------
# 10. JSON con texto alrededor → se parsea igual (vía extract_json_array)
# ---------------------------------------------------------------------------


async def test_llm_devuelve_json_con_preamble_se_parsea(
    mock_llm, mock_memory, mock_embedder
):
    seed = _entry("s1", "algo")
    vecino = _entry("v1", "similar")
    mock_memory.load_unreconciled.return_value = [seed]
    mock_memory.search_with_scores.return_value = [(seed, 0.99), (vecino, 0.85)]
    mock_memory.delete.return_value = vecino

    # El LLM agrega texto alrededor del JSON
    raw = (
        'Aquí está mi análisis del cluster:\n'
        '[{"action": "supersede", "target_ids": ["v1"]}]\n'
        'No hay nada más que decir.'
    )
    from core.domain.value_objects.llm_response import LLMResponse

    mock_llm.complete.return_value = LLMResponse.of_text(raw)

    uc = _make_uc(mock_llm, mock_memory, mock_embedder)
    await uc.execute()

    # Debe haber aplicado el supersede
    mock_memory.delete.assert_called_once_with("v1")


# ---------------------------------------------------------------------------
# 11. LLM devuelve basura no-JSON → cluster se saltea (best-effort)
# ---------------------------------------------------------------------------


async def test_llm_devuelve_basura_saltea_cluster_y_sigue(
    mock_llm, mock_memory, mock_embedder
):
    seed1 = _entry("s1", "algo")
    vecino1 = _entry("v1", "similar")
    seed2 = _entry("s2", "otro tema", channel="telegram", chat_id="200")
    # seed2 no tiene vecinos → se marca directamente
    mock_memory.load_unreconciled.return_value = [seed1, seed2]

    def fake_search(embedding, top_k):
        # Para seed1: devuelve vecino1 también
        # Para seed2: devuelve solo seed2 (sin vecinos)
        if embedding == seed1.embedding:
            return [(seed1, 0.99), (vecino1, 0.90)]
        return [(seed2, 0.99)]

    mock_memory.search_with_scores.side_effect = fake_search

    from core.domain.value_objects.llm_response import LLMResponse

    mock_llm.complete.return_value = LLMResponse.of_text("esto no es json para nada!@#$")

    uc = _make_uc(mock_llm, mock_memory, mock_embedder)
    await uc.execute()  # no debe lanzar

    # seed1 se marca reconciliado (aunque el LLM falló, best-effort)
    # seed2 se marca reconciliado (sin vecinos, path directo)
    calls = mock_memory.mark_reconciled.call_args_list
    todos_los_ids = [arg for call in calls for arg in call.args[0]]
    assert "s1" in todos_los_ids
    assert "s2" in todos_los_ids


# ---------------------------------------------------------------------------
# 12. set_reconciler → delega al one-shot en vez del LLM directo
# ---------------------------------------------------------------------------


async def test_set_reconciler_usa_one_shot_en_vez_de_llm(
    mock_llm, mock_memory, mock_embedder
):
    seed = _entry("s1", "algo")
    vecino = _entry("v1", "similar")
    mock_memory.load_unreconciled.return_value = [seed]
    mock_memory.search_with_scores.return_value = [(seed, 0.99), (vecino, 0.90)]

    mock_one_shot = AsyncMock()
    mock_one_shot.execute.return_value = "[]"

    uc = _make_uc(mock_llm, mock_memory, mock_embedder)
    uc.set_reconciler(mock_one_shot, max_iterations=3, timeout_seconds=60)

    await uc.execute()

    # El one-shot se invocó; el LLM directo NO
    mock_one_shot.execute.assert_called_once()
    mock_llm.complete.assert_not_called()

    # Verifica parámetros del one-shot
    call_kwargs = mock_one_shot.execute.call_args.kwargs
    assert call_kwargs.get("system_prompt") is None  # usa el del sub-agente
    assert call_kwargs.get("max_iterations") == 3
    assert call_kwargs.get("timeout_seconds") == 60


# ---------------------------------------------------------------------------
# 13. Anti-doble-proceso: id ya en procesados no se re-procesa
# ---------------------------------------------------------------------------


async def test_id_procesado_no_se_reprocesa(mock_llm, mock_memory, mock_embedder):
    """Si un id aparece como seed Y como vecino de otro seed anterior, se saltea."""
    seed1 = _entry("s1", "algo A")
    seed2 = _entry("s2", "algo B")  # también es vecino de s1
    mock_memory.load_unreconciled.return_value = [seed1, seed2]

    def fake_search(embedding, top_k):
        # s1 tiene a s2 como vecino (score alto)
        if embedding == seed1.embedding:
            return [(seed1, 0.99), (seed2, 0.92)]
        return [(seed2, 0.99)]

    mock_memory.search_with_scores.side_effect = fake_search
    mock_memory.delete.return_value = seed2

    accion = {
        "action": "merge",
        "target_ids": ["s2"],
        "new_content": "fusionado",
        "new_relevance": 0.85,
        "new_tags": [],
    }
    from core.domain.value_objects.llm_response import LLMResponse

    mock_llm.complete.return_value = LLMResponse.of_text(json.dumps([accion]))
    mock_embedder.embed_passage.return_value = [0.2] * 384

    uc = _make_uc(mock_llm, mock_memory, mock_embedder)
    await uc.execute()

    # El LLM solo se llama UNA vez (para el cluster de s1)
    # s2 ya está en procesados cuando le toca ser seed → se saltea
    mock_llm.complete.assert_called_once()
    # store solo se llama una vez (el merge de s1+s2)
    mock_memory.store.assert_called_once()


# ---------------------------------------------------------------------------
# 14. Resumen con contadores correctos
# ---------------------------------------------------------------------------


async def test_resumen_contiene_contadores(mock_llm, mock_memory, mock_embedder):
    seed = _entry("s1", "algo")
    vecino = _entry("v1", "similar")
    mock_memory.load_unreconciled.return_value = [seed]
    mock_memory.search_with_scores.return_value = [(seed, 0.99), (vecino, 0.88)]
    mock_memory.delete.return_value = vecino

    accion = {
        "action": "merge",
        "target_ids": ["v1"],
        "new_content": "fusionado",
        "new_relevance": 0.85,
        "new_tags": [],
    }
    from core.domain.value_objects.llm_response import LLMResponse

    mock_llm.complete.return_value = LLMResponse.of_text(json.dumps([accion]))
    mock_embedder.embed_passage.return_value = [0.2] * 384

    uc = _make_uc(mock_llm, mock_memory, mock_embedder)
    result = await uc.execute()

    assert "1" in result  # merges
    assert "merge" in result.lower()
