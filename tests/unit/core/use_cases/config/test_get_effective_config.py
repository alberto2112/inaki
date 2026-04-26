"""Tests unitarios para GetEffectiveConfigUseCase."""

from __future__ import annotations

from unittest.mock import MagicMock

from core.ports.config_repository import LayerName
from core.use_cases.config.get_effective_config import GetEffectiveConfigUseCase, OrigenCampo


def _repo_con_capas(capas: dict[tuple, dict]) -> MagicMock:
    """Genera un mock que devuelve datos por (layer, agent_id)."""
    from unittest.mock import MagicMock
    from core.ports.config_repository import IConfigRepository

    repo = MagicMock(spec=IConfigRepository)

    def read_layer(layer: LayerName, agent_id: str | None = None) -> dict:
        return capas.get((layer, agent_id), {})

    repo.read_layer.side_effect = read_layer
    return repo


# ---------------------------------------------------------------------------
# Solo capas globales (sin agente)
# ---------------------------------------------------------------------------


def test_solo_global_sin_agente() -> None:
    """Un campo definido solo en global → origen 'global'."""
    repo = _repo_con_capas(
        {
            (LayerName.GLOBAL, None): {"llm": {"model": "claude-3"}},
        }
    )
    uc = GetEffectiveConfigUseCase(repo)
    resultado = uc.execute(agent_id=None)

    assert resultado.datos["llm"]["model"] == "claude-3"
    assert resultado.origenes["llm.model"] == OrigenCampo(capa="global")


def test_global_secrets_pisa_global() -> None:
    """Un campo en global.secrets pisa al de global → origen 'global.secrets'."""
    repo = _repo_con_capas(
        {
            (LayerName.GLOBAL, None): {"providers": {"groq": {"api_key": "old"}}},
            (LayerName.GLOBAL_SECRETS, None): {"providers": {"groq": {"api_key": "new"}}},
        }
    )
    uc = GetEffectiveConfigUseCase(repo)
    resultado = uc.execute(agent_id=None)

    assert resultado.datos["providers"]["groq"]["api_key"] == "new"
    assert resultado.origenes["providers.groq.api_key"] == OrigenCampo(capa="global.secrets")


def test_capa_vacia_ignorada() -> None:
    """Si una capa devuelve dict vacío, no rompe el merge."""
    repo = _repo_con_capas(
        {
            (LayerName.GLOBAL, None): {"app": {"name": "Iñaki"}},
            (LayerName.GLOBAL_SECRETS, None): {},
        }
    )
    uc = GetEffectiveConfigUseCase(repo)
    resultado = uc.execute(agent_id=None)

    assert resultado.datos["app"]["name"] == "Iñaki"


# ---------------------------------------------------------------------------
# Con agente
# ---------------------------------------------------------------------------


def test_agent_pisa_global() -> None:
    """Un campo en la capa agent pisa al de global → origen 'agent'."""
    repo = _repo_con_capas(
        {
            (LayerName.GLOBAL, None): {"llm": {"model": "default-model", "temperature": 0.7}},
            (LayerName.GLOBAL_SECRETS, None): {},
            (LayerName.AGENT, "dev"): {"llm": {"model": "agente-model"}},
            (LayerName.AGENT_SECRETS, "dev"): {},
        }
    )
    uc = GetEffectiveConfigUseCase(repo)
    resultado = uc.execute(agent_id="dev")

    # El agente pisa el modelo
    assert resultado.datos["llm"]["model"] == "agente-model"
    assert resultado.origenes["llm.model"] == OrigenCampo(capa="agent")
    # La temperatura viene de global (no la pisó el agente)
    assert resultado.datos["llm"]["temperature"] == 0.7


def test_agent_secrets_pisa_agent() -> None:
    """agent.secrets tiene mayor prioridad que agent."""
    repo = _repo_con_capas(
        {
            (LayerName.GLOBAL, None): {},
            (LayerName.GLOBAL_SECRETS, None): {},
            (LayerName.AGENT, "dev"): {"channels": {"telegram": {"token": "old-token"}}},
            (LayerName.AGENT_SECRETS, "dev"): {
                "channels": {"telegram": {"token": "secret-token"}}
            },
        }
    )
    uc = GetEffectiveConfigUseCase(repo)
    resultado = uc.execute(agent_id="dev")

    assert resultado.datos["channels"]["telegram"]["token"] == "secret-token"
    assert resultado.origenes["channels.telegram.token"] == OrigenCampo(capa="agent.secrets")


def test_sin_agente_no_lee_capas_de_agente() -> None:
    """Con agent_id=None, no se llaman las capas de agente."""
    from unittest.mock import MagicMock
    from core.ports.config_repository import IConfigRepository

    repo = MagicMock(spec=IConfigRepository)
    repo.read_layer.return_value = {}

    uc = GetEffectiveConfigUseCase(repo)
    uc.execute(agent_id=None)

    # Solo deben haberse llamado las capas globales
    llamadas = [call[0][0] for call in repo.read_layer.call_args_list]
    assert LayerName.AGENT not in llamadas
    assert LayerName.AGENT_SECRETS not in llamadas
