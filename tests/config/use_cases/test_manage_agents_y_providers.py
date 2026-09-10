"""``ManageAgentsUseCase`` y ``ManageProvidersUseCase``: validados, con rollback y con guards."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from inaki.config.adapters.yaml_repository import YamlRepository
from inaki.config.use_cases.apply_changes import ConfigInvalidaError
from inaki.config.use_cases.manage_agents import EntidadEnUsoError, ManageAgentsUseCase
from inaki.config.use_cases.manage_providers import ManageProvidersUseCase
from inaki.shared.errors import AgentNotFoundError, AgentYaExisteError


@pytest.fixture
def repo(tmp_path: Path) -> YamlRepository:
    (tmp_path / "config").mkdir()
    (tmp_path / "agents" / "sub-agents").mkdir(parents=True)
    (tmp_path / "config" / "global.yaml").write_text(
        "app:\n  default_agent: dev\n"
        "providers:\n  openrouter:\n    api_key: sk\n  groq:\n    api_key: gk\n"
        "llm:\n  provider: openrouter\n"
    )
    (tmp_path / "agents" / "dev.yaml").write_text("id: dev\nname: Dev\n")
    (tmp_path / "agents" / "otro.yaml").write_text(
        "id: otro\nname: Otro\ntranscription:\n  provider: groq\n"
    )
    return YamlRepository(config_dir=tmp_path / "config", agents_dir=tmp_path / "agents")


def _rechaza(msg: str) -> MagicMock:
    return MagicMock(side_effect=ValueError(msg))


# --- agentes -----------------------------------------------------------------


def test_crear_escribe_la_plantilla_y_el_sub_agente_va_a_su_directorio(
    tmp_path: Path, repo: YamlRepository
) -> None:
    uc = ManageAgentsUseCase(repo, validar=MagicMock())
    uc.crear("nuevo", "Nuevo", descripcion="d", system_prompt="Sos Nuevo.")
    uc.crear("worker", "Worker", sub_agente=True)

    a = yaml.safe_load((tmp_path / "agents" / "nuevo.yaml").read_text())
    assert a == {"id": "nuevo", "name": "Nuevo", "description": "d", "system_prompt": "Sos Nuevo."}
    assert (tmp_path / "agents" / "sub-agents" / "worker.yaml").exists()
    assert repo.list_agents() == ["dev", "nuevo", "otro"] and repo.list_sub_agents() == ["worker"]


def test_crear_que_el_loader_rechaza_no_deja_fichero(tmp_path: Path, repo: YamlRepository) -> None:
    uc = ManageAgentsUseCase(repo, validar=_rechaza("token de Telegram duplicado"))
    with pytest.raises(ConfigInvalidaError, match="duplicado"):
        uc.crear("nuevo", "Nuevo")
    assert not (tmp_path / "agents" / "nuevo.yaml").exists()


def test_crear_con_id_ocupado_no_toca_nada(tmp_path: Path, repo: YamlRepository) -> None:
    validar = MagicMock()
    with pytest.raises(AgentYaExisteError):
        ManageAgentsUseCase(repo, validar).crear("dev", "Otro Dev")
    validar.assert_not_called()
    assert yaml.safe_load((tmp_path / "agents" / "dev.yaml").read_text())["name"] == "Dev"


def test_borrar_restaura_el_yaml_si_el_loader_rechaza(tmp_path: Path, repo: YamlRepository) -> None:
    uc = ManageAgentsUseCase(repo, validar=_rechaza("algo dependía de 'otro'"))
    with pytest.raises(ConfigInvalidaError):
        uc.borrar("otro")
    assert yaml.safe_load((tmp_path / "agents" / "otro.yaml").read_text())["name"] == "Otro"


def test_no_se_borra_el_default_agent_ni_uno_inexistente(repo: YamlRepository) -> None:
    uc = ManageAgentsUseCase(repo, validar=MagicMock())
    with pytest.raises(EntidadEnUsoError, match="default_agent"):
        uc.borrar("dev")
    with pytest.raises(AgentNotFoundError):
        uc.borrar("fantasma")
    uc.borrar("otro")
    assert repo.list_agents() == ["dev"]


# --- providers ---------------------------------------------------------------


def test_guardar_crea_o_edita_sin_pisar_la_api_key_si_viene_vacia(
    tmp_path: Path, repo: YamlRepository
) -> None:
    uc = ManageProvidersUseCase(repo, validar=MagicMock())
    uc.guardar("ollama", base_url="http://pi:11434")
    uc.guardar("openrouter", api_key="", type="openrouter")

    g = yaml.safe_load((tmp_path / "config" / "global.yaml").read_text())
    assert g["providers"]["ollama"] == {"base_url": "http://pi:11434"}
    assert g["providers"]["openrouter"] == {"api_key": "sk", "type": "openrouter"}
    assert [p.key for p in uc.listar()] == ["groq", "ollama", "openrouter"]
    assert not any(hasattr(p, "api_key") for p in uc.listar()), "listar nunca expone la credencial"


def test_guardar_que_el_loader_rechaza_restaura_global(
    tmp_path: Path, repo: YamlRepository
) -> None:
    antes = (tmp_path / "config" / "global.yaml").read_text()
    uc = ManageProvidersUseCase(repo, validar=_rechaza("providers.x: type desconocido"))
    with pytest.raises(ConfigInvalidaError):
        uc.guardar("x", type="no-existe")
    assert yaml.safe_load((tmp_path / "config" / "global.yaml").read_text()) == yaml.safe_load(
        antes
    )


def test_no_se_borra_un_provider_referenciado_en_global_o_en_un_agente(
    repo: YamlRepository,
) -> None:
    uc = ManageProvidersUseCase(repo, validar=MagicMock())
    with pytest.raises(EntidadEnUsoError, match="global:llm.provider"):
        uc.borrar("openrouter")
    with pytest.raises(EntidadEnUsoError, match="otro:transcription.provider"):
        uc.borrar("groq")
    assert uc.referencias("groq") == [("otro", "transcription.provider")]


def test_borrar_un_provider_libre_se_lleva_la_credencial(
    tmp_path: Path, repo: YamlRepository
) -> None:
    uc = ManageProvidersUseCase(repo, validar=MagicMock())
    uc.guardar("libre", api_key="secreto-libre-123")
    assert "secreto-libre-123" in (tmp_path / "config" / "global.yaml").read_text()
    uc.borrar("libre")
    g = yaml.safe_load((tmp_path / "config" / "global.yaml").read_text())
    assert "libre" not in g["providers"]
    assert "secreto-libre-123" not in (tmp_path / "config" / "global.yaml").read_text()
