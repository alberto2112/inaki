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
# Solo capa global (sin agente)
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


def test_credenciales_globales_tienen_origen_global() -> None:
    """La api_key de un provider vive en global.yaml → origen 'global'.

    Antes había una capa ``global.secrets`` que la pisaba; ahora la credencial
    es un campo más de la capa global.
    """
    repo = _repo_con_capas(
        {
            (LayerName.GLOBAL, None): {"providers": {"groq": {"api_key": "gsk_secret"}}},
        }
    )
    uc = GetEffectiveConfigUseCase(repo)
    resultado = uc.execute(agent_id=None)

    assert resultado.datos["providers"]["groq"]["api_key"] == "gsk_secret"
    assert resultado.origenes["providers.groq.api_key"] == OrigenCampo(capa="global")


def test_capa_vacia_ignorada() -> None:
    """Si una capa devuelve dict vacío, no rompe el merge."""
    repo = _repo_con_capas(
        {
            (LayerName.GLOBAL, None): {"app": {"name": "Inaki"}},
            (LayerName.AGENT, "dev"): {},
        }
    )
    uc = GetEffectiveConfigUseCase(repo)
    resultado = uc.execute(agent_id="dev")

    assert resultado.datos["app"]["name"] == "Inaki"


# ---------------------------------------------------------------------------
# Con agente
# ---------------------------------------------------------------------------


def test_agent_pisa_global() -> None:
    """Un campo en la capa agent pisa al de global → origen 'agent'."""
    repo = _repo_con_capas(
        {
            (LayerName.GLOBAL, None): {"llm": {"model": "default-model", "temperature": 0.7}},
            (LayerName.AGENT, "dev"): {"llm": {"model": "agente-model"}},
        }
    )
    uc = GetEffectiveConfigUseCase(repo)
    resultado = uc.execute(agent_id="dev")

    # El agente pisa el modelo
    assert resultado.datos["llm"]["model"] == "agente-model"
    assert resultado.origenes["llm.model"] == OrigenCampo(capa="agent")
    # La temperatura viene de global (no la pisó el agente)
    assert resultado.datos["llm"]["temperature"] == 0.7


def test_credenciales_del_agente_tienen_origen_agent() -> None:
    """El token del canal vive en agents/{id}.yaml → origen 'agent'.

    Antes había una capa ``agent.secrets`` con mayor prioridad; ahora la
    credencial es un campo más de la capa del agente.
    """
    repo = _repo_con_capas(
        {
            (LayerName.GLOBAL, None): {},
            (LayerName.AGENT, "dev"): {"channels": {"telegram": {"token": "secret-token"}}},
        }
    )
    uc = GetEffectiveConfigUseCase(repo)
    resultado = uc.execute(agent_id="dev")

    assert resultado.datos["channels"]["telegram"]["token"] == "secret-token"
    assert resultado.origenes["channels.telegram.token"] == OrigenCampo(capa="agent")


def test_cadena_de_capas_es_global_y_agent() -> None:
    """Con agent_id, la cadena leída es exactamente global → agent."""
    repo = _repo_con_capas({})
    uc = GetEffectiveConfigUseCase(repo)
    uc.execute(agent_id="dev")

    llamadas = [call[0][0] for call in repo.read_layer.call_args_list]
    assert llamadas == [LayerName.GLOBAL, LayerName.AGENT]


def test_sin_agente_no_lee_capas_de_agente() -> None:
    """Con agent_id=None, no se llaman las capas de agente."""
    from unittest.mock import MagicMock
    from core.ports.config_repository import IConfigRepository

    repo = MagicMock(spec=IConfigRepository)
    repo.read_layer.return_value = {}

    uc = GetEffectiveConfigUseCase(repo)
    uc.execute(agent_id=None)

    # Solo debe haberse llamado la capa global
    llamadas = [call[0][0] for call in repo.read_layer.call_args_list]
    assert llamadas == [LayerName.GLOBAL]
    assert LayerName.AGENT not in llamadas
