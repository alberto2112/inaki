"""Fixtures compartidas para los tests de use cases de config."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.ports.config_repository import IConfigRepository


def _make_repo() -> MagicMock:
    """Crea un mock de IConfigRepository con comportamiento por defecto."""
    repo = MagicMock(spec=IConfigRepository)
    # Por defecto: read_layer devuelve dict vacío, layer_exists devuelve False.
    repo.read_layer.return_value = {}
    repo.layer_exists.return_value = False
    repo.list_agents.return_value = []
    repo.render_yaml.return_value = ""
    return repo


@pytest.fixture()
def repo() -> MagicMock:
    """Mock de IConfigRepository."""
    return _make_repo()
