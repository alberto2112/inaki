"""Tests para ``InMemoryScopeRegistryAdapter``.

Cubre los tres comportamientos críticos del contrato ``IScopeRegistry``:
1. ``try_mark_busy`` es atómico entre corutinas concurrentes.
2. ``mark_idle`` es idempotente y libera el scope correctamente.
3. Scopes distintos no interfieren entre sí.
"""

from __future__ import annotations

import asyncio

import pytest

from adapters.outbound.scope_registry_adapter import InMemoryScopeRegistryAdapter
from core.ports.outbound.scope_registry_port import Scope


@pytest.fixture
def registry() -> InMemoryScopeRegistryAdapter:
    return InMemoryScopeRegistryAdapter()


@pytest.fixture
def scope() -> Scope:
    return ("agent1", "telegram", "chat1")


async def test_try_mark_busy_idle_scope_returns_true(
    registry: InMemoryScopeRegistryAdapter,
    scope: Scope,
) -> None:
    """Un scope nuevo se puede marcar ocupado sin problema."""
    assert await registry.try_mark_busy(scope) is True


async def test_try_mark_busy_already_busy_returns_false(
    registry: InMemoryScopeRegistryAdapter,
    scope: Scope,
) -> None:
    """Si ya está ocupado, el segundo caller recibe False."""
    await registry.try_mark_busy(scope)
    assert await registry.try_mark_busy(scope) is False


async def test_mark_idle_releases_scope(
    registry: InMemoryScopeRegistryAdapter,
    scope: Scope,
) -> None:
    """Después de mark_idle, el scope se puede volver a marcar ocupado."""
    await registry.try_mark_busy(scope)
    await registry.mark_idle(scope)
    assert await registry.try_mark_busy(scope) is True


async def test_mark_idle_no_op_if_not_busy(
    registry: InMemoryScopeRegistryAdapter,
    scope: Scope,
) -> None:
    """Llamar mark_idle sin try_mark_busy previo es no-op (no lanza)."""
    # No debe lanzar excepción.
    await registry.mark_idle(scope)
    # Estado debe ser consistente: el scope sigue libre.
    assert await registry.try_mark_busy(scope) is True


async def test_different_scopes_are_independent(
    registry: InMemoryScopeRegistryAdapter,
) -> None:
    """Marcar ocupado un scope NO afecta a otros scopes distintos."""
    scope_a: Scope = ("agent1", "telegram", "chat1")
    scope_b: Scope = ("agent1", "telegram", "chat2")
    scope_c: Scope = ("agent2", "telegram", "chat1")

    await registry.try_mark_busy(scope_a)

    # B y C son distintos a A — deben poder marcarse.
    assert await registry.try_mark_busy(scope_b) is True
    assert await registry.try_mark_busy(scope_c) is True
    # A sigue ocupado.
    assert await registry.try_mark_busy(scope_a) is False


async def test_concurrent_acquire_only_one_wins(
    registry: InMemoryScopeRegistryAdapter,
    scope: Scope,
) -> None:
    """50 corutinas compiten por el mismo scope: exactamente UNA gana.

    Este test verifica el invariante atómico de ``try_mark_busy``: no
    importa cuán intercaladas corran las corutinas, solo una debe
    recibir True. Si el lock no protegiera la transición check+add,
    múltiples corutinas verían el set vacío al mismo tiempo y todas
    devolverían True.
    """
    results = await asyncio.gather(*[registry.try_mark_busy(scope) for _ in range(50)])
    assert results.count(True) == 1
    assert results.count(False) == 49


async def test_acquire_release_cycle(
    registry: InMemoryScopeRegistryAdapter,
    scope: Scope,
) -> None:
    """Ciclo completo: acquire → release → acquire → release."""
    assert await registry.try_mark_busy(scope) is True
    assert await registry.try_mark_busy(scope) is False  # ya ocupado
    await registry.mark_idle(scope)
    assert await registry.try_mark_busy(scope) is True  # libre de nuevo
    await registry.mark_idle(scope)
