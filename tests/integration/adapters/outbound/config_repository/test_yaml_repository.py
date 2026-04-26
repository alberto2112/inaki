"""
Tests de integración para YamlRepository.

Validan el comportamiento de round-trip de ruamel.yaml:
- Archivos con comentarios simples, anidados, listas, anchors y bloques comentados.
- Escritura atómica (tmp → replace).
- Permisos 600 en archivos de secrets.
- Idempotencia de delete_layer.
- Manejo de archivos inexistentes (devuelve {}).
- Mutación profunda preservando comentarios de otros campos.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from adapters.outbound.config_repository import YamlRepository
from core.ports.config_repository import LayerName

# Ruta a los fixtures YAML
FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Fixtures de pytest
# ---------------------------------------------------------------------------


@pytest.fixture()
def repo(tmp_path: Path) -> YamlRepository:
    """Repositorio apuntando a un directorio temporal aislado por test."""
    return YamlRepository(config_dir=tmp_path)


@pytest.fixture()
def repo_agentes(tmp_path: Path) -> YamlRepository:
    """Repositorio con subdirectorio agents/ pre-creado."""
    (tmp_path / "agents").mkdir()
    return YamlRepository(config_dir=tmp_path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cargar_fixture(nombre: str) -> str:
    """Lee el contenido crudo de un fixture YAML."""
    return (FIXTURES / nombre).read_text(encoding="utf-8")


def _escribir_capa_desde_fixture(repo: YamlRepository, layer: LayerName, fixture: str) -> str:
    """
    Lee un fixture, lo carga con ruamel, y lo escribe como capa global.
    Retorna el contenido original del fixture para comparación.
    """
    original = _cargar_fixture(fixture)
    # Escribimos el contenido original directamente al path para simular
    # un archivo pre-existente del usuario
    path = repo._layer_path(layer, None)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(original, encoding="utf-8")
    return original


# ---------------------------------------------------------------------------
# Tests: archivo inexistente
# ---------------------------------------------------------------------------


def test_read_layer_archivo_inexistente_devuelve_vacio(repo: YamlRepository) -> None:
    """read_layer devuelve {} si el archivo no existe."""
    resultado = repo.read_layer(LayerName.GLOBAL)
    assert resultado == {}


def test_layer_exists_falso_si_no_existe(repo: YamlRepository) -> None:
    """layer_exists devuelve False si el archivo aún no fue creado."""
    assert not repo.layer_exists(LayerName.GLOBAL)


def test_list_agents_vacio_si_no_hay_directorio(repo: YamlRepository) -> None:
    """list_agents devuelve [] si el directorio de agentes no existe."""
    assert repo.list_agents() == []


# ---------------------------------------------------------------------------
# Tests: escritura básica y lectura
# ---------------------------------------------------------------------------


def test_write_y_read_layer_global(repo: YamlRepository) -> None:
    """Escribe y relée la capa global, los valores deben coincidir."""
    datos = {"log_level": "DEBUG", "name": "Test"}
    repo.write_layer(LayerName.GLOBAL, datos)
    leido = repo.read_layer(LayerName.GLOBAL)
    assert leido["log_level"] == "DEBUG"
    assert leido["name"] == "Test"


def test_write_crea_archivo_si_no_existe(repo: YamlRepository) -> None:
    """write_layer crea el archivo cuando no existe previamente."""
    assert not repo.layer_exists(LayerName.GLOBAL)
    repo.write_layer(LayerName.GLOBAL, {"key": "val"})
    assert repo.layer_exists(LayerName.GLOBAL)


def test_write_archivo_nuevo_contiene_header(repo: YamlRepository) -> None:
    """El archivo creado nuevo debe tener un comentario de header."""
    repo.write_layer(LayerName.GLOBAL, {"x": 1})
    contenido = repo._layer_path(LayerName.GLOBAL, None).read_text(encoding="utf-8")
    assert "# Generado por inaki setup" in contenido


def test_write_secrets_permisos_600(repo: YamlRepository) -> None:
    """Los archivos de secrets se crean con permisos 600."""
    repo.write_layer(LayerName.GLOBAL_SECRETS, {"providers": {}})
    path = repo._layer_path(LayerName.GLOBAL_SECRETS, None)
    modo = oct(stat.S_IMODE(os.stat(path).st_mode))
    assert modo == "0o600", f"Permisos esperados 0o600, obtenidos: {modo}"


def test_write_agent_secrets_permisos_600(repo_agentes: YamlRepository) -> None:
    """Los archivos de secrets de agente se crean con permisos 600."""
    repo_agentes.write_layer(LayerName.AGENT_SECRETS, {"token": "abc"}, agent_id="general")
    path = repo_agentes._layer_path(LayerName.AGENT_SECRETS, "general")
    modo = oct(stat.S_IMODE(os.stat(path).st_mode))
    assert modo == "0o600", f"Permisos esperados 0o600, obtenidos: {modo}"


# ---------------------------------------------------------------------------
# Tests: agentes
# ---------------------------------------------------------------------------


def test_write_y_list_agents(repo: YamlRepository) -> None:
    """Escribe dos agentes y los enumera correctamente."""
    repo.write_layer(LayerName.AGENT, {"id": "dev", "name": "Dev"}, agent_id="dev")
    repo.write_layer(LayerName.AGENT, {"id": "general", "name": "General"}, agent_id="general")
    agentes = repo.list_agents()
    assert agentes == ["dev", "general"]


def test_list_agents_excluye_secrets_y_example(repo: YamlRepository) -> None:
    """list_agents no incluye *.secrets.yaml ni *.example.yaml."""
    agents_dir = repo._agents_dir
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / "general.yaml").write_text("id: general", encoding="utf-8")
    (agents_dir / "general.secrets.yaml").write_text("token: abc", encoding="utf-8")
    (agents_dir / "template.example.yaml").write_text("id: template", encoding="utf-8")
    agentes = repo.list_agents()
    assert agentes == ["general"]


def test_agent_id_vacio_levanta_error(repo: YamlRepository) -> None:
    """Usar agent_id vacío en capas de agente debe levantar ValueError."""
    with pytest.raises(ValueError, match="agent_id"):
        repo.read_layer(LayerName.AGENT, agent_id="")

    with pytest.raises(ValueError, match="agent_id"):
        repo.write_layer(LayerName.AGENT, {}, agent_id=None)


# ---------------------------------------------------------------------------
# Tests: delete_layer
# ---------------------------------------------------------------------------


def test_delete_layer_elimina_archivo(repo: YamlRepository) -> None:
    """delete_layer elimina el archivo existente."""
    repo.write_layer(LayerName.GLOBAL, {"key": "val"})
    assert repo.layer_exists(LayerName.GLOBAL)
    repo.delete_layer(LayerName.GLOBAL)
    assert not repo.layer_exists(LayerName.GLOBAL)


def test_delete_layer_idempotente(repo: YamlRepository) -> None:
    """delete_layer es no-op si el archivo no existe — no lanza error."""
    repo.delete_layer(LayerName.GLOBAL)  # archivo no existe
    repo.delete_layer(LayerName.GLOBAL)  # segunda vez también ok


def test_delete_agent_layer(repo_agentes: YamlRepository) -> None:
    """Elimina una capa de agente por id."""
    repo_agentes.write_layer(LayerName.AGENT, {"id": "test"}, agent_id="test")
    assert repo_agentes.layer_exists(LayerName.AGENT, "test")
    repo_agentes.delete_layer(LayerName.AGENT, "test")
    assert not repo_agentes.layer_exists(LayerName.AGENT, "test")
    # El agente desaparece de la lista
    assert "test" not in repo_agentes.list_agents()


# ---------------------------------------------------------------------------
# Tests: round-trip — simple
# ---------------------------------------------------------------------------


def test_round_trip_simple_byte_identico(repo: YamlRepository) -> None:
    """
    Carga un YAML simple, lo escribe sin cambios, y verifica que el resultado
    es byte-idéntico al original.
    """
    original = _escribir_capa_desde_fixture(repo, LayerName.GLOBAL, "simple.yaml")
    data = repo.read_layer(LayerName.GLOBAL)
    repo.write_layer(LayerName.GLOBAL, data)
    resultado = repo._layer_path(LayerName.GLOBAL, None).read_text(encoding="utf-8")
    assert resultado == original, "El round-trip alteró el contenido del archivo"


def test_round_trip_simple_preserva_comentarios_tras_mutacion(repo: YamlRepository) -> None:
    """
    Muta un campo del YAML simple y verifica que los comentarios de los
    otros campos se preservan intactos.
    """
    _escribir_capa_desde_fixture(repo, LayerName.GLOBAL, "simple.yaml")
    data = repo.read_layer(LayerName.GLOBAL)
    data["version"] = 99
    repo.write_layer(LayerName.GLOBAL, data)
    contenido = repo._layer_path(LayerName.GLOBAL, None).read_text(encoding="utf-8")
    assert "version: 99" in contenido
    assert "# Nivel de log del sistema" in contenido
    assert "# Nombre para mostrar al usuario" in contenido
    assert "# Versión de la config" in contenido


# ---------------------------------------------------------------------------
# Tests: round-trip — nested
# ---------------------------------------------------------------------------


def test_round_trip_nested_byte_identico(repo: YamlRepository) -> None:
    """Round-trip byte-idéntico para YAML anidado con comentarios."""
    original = _escribir_capa_desde_fixture(repo, LayerName.GLOBAL, "nested.yaml")
    data = repo.read_layer(LayerName.GLOBAL)
    repo.write_layer(LayerName.GLOBAL, data)
    resultado = repo._layer_path(LayerName.GLOBAL, None).read_text(encoding="utf-8")
    assert resultado == original


def test_round_trip_nested_mutacion_hoja_profunda(repo: YamlRepository) -> None:
    """
    Muta una clave hoja profunda (llm.model) y verifica que los comentarios
    del bloque app y del resto de llm se mantienen.
    """
    _escribir_capa_desde_fixture(repo, LayerName.GLOBAL, "nested.yaml")
    data = repo.read_layer(LayerName.GLOBAL)
    data["llm"]["model"] = "gpt-4o"
    repo.write_layer(LayerName.GLOBAL, data)
    contenido = repo._layer_path(LayerName.GLOBAL, None).read_text(encoding="utf-8")
    assert "model: gpt-4o" in contenido
    assert "# Bloque de configuración del LLM" in contenido
    assert "# Bloque de configuración de la aplicación" in contenido
    assert "# Provider externo a usar" in contenido


# ---------------------------------------------------------------------------
# Tests: round-trip — listas
# ---------------------------------------------------------------------------


def test_round_trip_listas_byte_identico(repo: YamlRepository) -> None:
    """Round-trip byte-idéntico para YAML con listas y dicts dentro."""
    original = _escribir_capa_desde_fixture(repo, LayerName.GLOBAL, "with_lists.yaml")
    data = repo.read_layer(LayerName.GLOBAL)
    repo.write_layer(LayerName.GLOBAL, data)
    resultado = repo._layer_path(LayerName.GLOBAL, None).read_text(encoding="utf-8")
    assert resultado == original


def test_round_trip_listas_mutacion_preserva_comentarios(repo: YamlRepository) -> None:
    """
    Agrega un proveedor y verifica que los comentarios de los proveedores
    existentes se mantienen.
    """
    _escribir_capa_desde_fixture(repo, LayerName.GLOBAL, "with_lists.yaml")
    data = repo.read_layer(LayerName.GLOBAL)
    # Modifica un valor existente
    data["providers"]["openai"]["api_key"] = "sk-nuevo-key"
    repo.write_layer(LayerName.GLOBAL, data)
    contenido = repo._layer_path(LayerName.GLOBAL, None).read_text(encoding="utf-8")
    assert "sk-nuevo-key" in contenido
    assert "# OpenRouter — acceso a múltiples modelos" in contenido
    assert "# Groq — inferencia ultra-rápida" in contenido


# ---------------------------------------------------------------------------
# Tests: round-trip — anchors
# ---------------------------------------------------------------------------


def test_round_trip_anchors_byte_identico(repo: YamlRepository) -> None:
    """Round-trip byte-idéntico para YAML con anchors y merge keys."""
    original = _escribir_capa_desde_fixture(repo, LayerName.GLOBAL, "with_anchors.yaml")
    data = repo.read_layer(LayerName.GLOBAL)
    repo.write_layer(LayerName.GLOBAL, data)
    resultado = repo._layer_path(LayerName.GLOBAL, None).read_text(encoding="utf-8")
    assert resultado == original


# ---------------------------------------------------------------------------
# Tests: round-trip — bloque comentado (caso principal del usuario)
# ---------------------------------------------------------------------------


def test_round_trip_bloque_comentado_byte_identico(repo: YamlRepository) -> None:
    """
    El caso de uso principal: el usuario tiene bloques comentados como
    alternativas (``# llm: ...``) y el round-trip NO debe tocarlos.
    """
    original = _escribir_capa_desde_fixture(repo, LayerName.GLOBAL, "commented_block.yaml")
    data = repo.read_layer(LayerName.GLOBAL)
    repo.write_layer(LayerName.GLOBAL, data)
    resultado = repo._layer_path(LayerName.GLOBAL, None).read_text(encoding="utf-8")
    assert resultado == original


def test_round_trip_bloque_comentado_preservado_tras_mutacion(repo: YamlRepository) -> None:
    """
    Muta llm.model (la clave activa) y verifica que el bloque comentado
    ``# llm: ... # provider: groq`` se mantiene intacto.
    """
    _escribir_capa_desde_fixture(repo, LayerName.GLOBAL, "commented_block.yaml")
    data = repo.read_layer(LayerName.GLOBAL)
    data["llm"]["model"] = "gpt-4-turbo"
    repo.write_layer(LayerName.GLOBAL, data)
    contenido = repo._layer_path(LayerName.GLOBAL, None).read_text(encoding="utf-8")
    # La mutación se aplicó
    assert "model: gpt-4-turbo" in contenido
    # El bloque comentado se preservó
    assert "# llm:" in contenido
    assert "#   provider: groq" in contenido
    assert "#   model: llama3-70b-8192" in contenido
    # El bloque comentado de embedding también se preservó
    assert "# embedding:" in contenido
    assert "#   provider: openai" in contenido


# ---------------------------------------------------------------------------
# Tests: render_yaml
# ---------------------------------------------------------------------------


def test_render_yaml_no_escribe_a_disco(repo: YamlRepository) -> None:
    """render_yaml devuelve string sin crear ningún archivo."""
    resultado = repo.render_yaml({"key": "value", "nested": {"a": 1}})
    assert "key: value" in resultado
    assert not repo.layer_exists(LayerName.GLOBAL)


def test_render_yaml_dict_plano(repo: YamlRepository) -> None:
    """render_yaml funciona con dicts planos básicos."""
    resultado = repo.render_yaml({"log_level": "INFO"})
    assert "log_level: INFO" in resultado


# ---------------------------------------------------------------------------
# Tests: IConfigRepository protocol check
# ---------------------------------------------------------------------------


def test_yaml_repository_implementa_protocolo(repo: YamlRepository) -> None:
    """YamlRepository debe ser una instancia del protocolo IConfigRepository."""
    from core.ports.config_repository import IConfigRepository

    assert isinstance(repo, IConfigRepository)
