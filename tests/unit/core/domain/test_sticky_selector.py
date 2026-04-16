"""Tests unitarios para apply_sticky — lógica pura del Sticky Union con TTL."""

from __future__ import annotations

from core.domain.services.sticky_selector import apply_sticky


# ---------------------------------------------------------------------------
# Feature deshabilitada
# ---------------------------------------------------------------------------


def test_ttl_zero_disables_feature():
    """Con ttl=0 el sticky queda completamente desactivado: sin estado persistido."""
    active, new_state = apply_sticky({"a", "b"}, {"c": 2}, ttl=0)
    assert active == {"a", "b"}
    assert new_state == {}


def test_ttl_negative_disables_feature():
    """ttl negativo trata como deshabilitado, no como error."""
    active, new_state = apply_sticky({"a"}, {"b": 1}, ttl=-1)
    assert active == {"a"}
    assert new_state == {}


# ---------------------------------------------------------------------------
# Primer turno — sin estado previo
# ---------------------------------------------------------------------------


def test_first_turn_no_prior_state():
    """Sin ttls previos, los ids del RAG entran con TTL completo."""
    active, new_state = apply_sticky({"a", "b"}, {}, ttl=3)
    assert active == {"a", "b"}
    assert new_state == {"a": 3, "b": 3}


def test_first_turn_empty_rag():
    """RAG vacío sin estado previo → active vacío, estado vacío."""
    active, new_state = apply_sticky(set(), {}, ttl=3)
    assert active == set()
    assert new_state == {}


# ---------------------------------------------------------------------------
# Refresh: id re-seleccionado resetea TTL al máximo
# ---------------------------------------------------------------------------


def test_rag_reselect_refreshes_ttl_to_full():
    """Si el RAG vuelve a elegir un sticky, su TTL se resetea a ttl."""
    active, new_state = apply_sticky({"a"}, {"a": 1}, ttl=3)
    assert active == {"a"}
    assert new_state == {"a": 3}


def test_rag_reselect_refresh_after_decrement():
    """Un sticky con TTL bajo se refresca al máximo si el RAG lo vuelve a elegir."""
    active, new_state = apply_sticky({"a", "b"}, {"a": 1, "b": 2}, ttl=5)
    assert active == {"a", "b"}
    assert new_state == {"a": 5, "b": 5}


# ---------------------------------------------------------------------------
# Decremento: sticky NO re-seleccionado pierde 1 turno
# ---------------------------------------------------------------------------


def test_sticky_not_reselected_decrements():
    """Un sticky que el RAG no re-selecciona pierde 1 turno de TTL."""
    active, new_state = apply_sticky(set(), {"a": 3}, ttl=5)
    assert active == {"a"}
    assert new_state == {"a": 2}


def test_sticky_expires_at_zero():
    """Un sticky con TTL=1 NO re-seleccionado se elimina (1 - 1 = 0)."""
    active, new_state = apply_sticky(set(), {"a": 1}, ttl=5)
    assert active == set()
    assert new_state == {}


def test_multiple_stickies_partial_expiry():
    """Varios stickies, unos expiran y otros sobreviven."""
    current = {"a": 1, "b": 3, "c": 2}
    active, new_state = apply_sticky(set(), current, ttl=5)
    # a expira (1 → 0), b y c decrementan
    assert active == {"b", "c"}
    assert new_state == {"b": 2, "c": 1}


# ---------------------------------------------------------------------------
# Unión: RAG + stickies supervivientes
# ---------------------------------------------------------------------------


def test_union_rag_and_surviving_stickies():
    """El conjunto activo es la UNIÓN de RAG actual y stickies supervivientes."""
    active, new_state = apply_sticky({"new_x"}, {"old_y": 2}, ttl=3)
    assert active == {"new_x", "old_y"}
    assert new_state == {"new_x": 3, "old_y": 1}


def test_union_rag_and_expiring_sticky():
    """Si un sticky expira en este turno, NO aparece en active ni en new_state."""
    active, new_state = apply_sticky({"new_x"}, {"old_y": 1}, ttl=3)
    assert active == {"new_x"}
    assert new_state == {"new_x": 3}


# ---------------------------------------------------------------------------
# Escenario completo: topic shift mid-conversation
# ---------------------------------------------------------------------------


def test_topic_shift_accumulates_then_drops():
    """
    Simula conversación: turno 1 agenda, turno 2 ambiguo, turno 3 poema.
    Verifica que agenda sobreviva el turno ambiguo y expire en el 3.
    """
    ttl = 2

    # Turno 1: RAG elige "agenda"
    active1, state1 = apply_sticky({"agenda"}, {}, ttl)
    assert active1 == {"agenda"}
    assert state1 == {"agenda": 2}

    # Turno 2: RAG no selecciona nada (input ambiguo "sí, hacelo")
    active2, state2 = apply_sticky(set(), state1, ttl)
    assert active2 == {"agenda"}  # sobrevive
    assert state2 == {"agenda": 1}

    # Turno 3: RAG elige "poema" (cambio de topic)
    active3, state3 = apply_sticky({"poema"}, state2, ttl)
    # agenda expira (1 → 0), poema entra fresco
    assert active3 == {"poema"}
    assert state3 == {"poema": 2}


def test_sustained_use_keeps_refreshing():
    """Uso sostenido de la misma skill → el TTL se mantiene al máximo."""
    ttl = 3
    state: dict[str, int] = {}
    for _ in range(5):
        _, state = apply_sticky({"agenda"}, state, ttl)
    assert state == {"agenda": 3}


# ---------------------------------------------------------------------------
# Inmutabilidad del input
# ---------------------------------------------------------------------------


def test_does_not_mutate_input_dict():
    """La función no debe mutar el dict de ttls de entrada."""
    current = {"a": 2, "b": 3}
    snapshot = dict(current)
    apply_sticky({"a"}, current, ttl=5)
    assert current == snapshot


def test_does_not_mutate_input_set():
    """La función no debe mutar el set de rag_ids de entrada."""
    rag_ids = {"a", "b"}
    snapshot = set(rag_ids)
    apply_sticky(rag_ids, {"c": 2}, ttl=3)
    assert rag_ids == snapshot
