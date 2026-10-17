"""Marcas de "no se cambia en runtime" del schema y su lectura por introspección.

Cubre las tareas 1 a 3 de ``config-set-efimero``: la marca ``FIJO_EN_RUNTIME`` de
los tipos de ruta, las marcas ``fijo_en_runtime`` de los campos y
``paths_fijos_en_runtime()``, que las junta con los secretos.
"""

from __future__ import annotations

from typing import Annotated, get_args

import pytest

import inaki.channels  # noqa: F401 — registra los canales: sus campos entran al schema
from inaki.config.schema._base import (
    FIJO_EN_RUNTIME,
    ExpandedPath,
    ExpandedPathList,
    RuntimePath,
)
from inaki.config import AgentConfig, GlobalConfig
from inaki.config.introspection import campos_del_schema, paths_fijos_en_runtime, paths_secretos
from inaki.config.schema.app import AppConfig
from inaki.config.schema.knowledge import KnowledgeConfig
from inaki.config.schema.workspace import WorkspaceConfig


class TestMarcaEnLosTiposDeRuta:
    def test_los_tres_tipos_de_ruta_llevan_la_marca_en_su_annotated(self):
        for tipo in (RuntimePath, ExpandedPath, ExpandedPathList):
            assert FIJO_EN_RUNTIME in get_args(tipo), tipo

    def test_un_campo_de_ruta_directo_conserva_la_marca_en_field_metadata(self):
        runtime = KnowledgeConfig.model_fields["db_dirname"]  # RuntimePath
        expandida = WorkspaceConfig.model_fields["path"]  # ExpandedPath

        assert FIJO_EN_RUNTIME in runtime.metadata
        assert FIJO_EN_RUNTIME in expandida.metadata

    def test_una_lista_de_rutas_lleva_la_marca_en_el_tipo_del_item(self):
        # list[RuntimePath]: pydantic no la sube a field.metadata, queda en get_args.
        ext_dirs = AppConfig.model_fields["ext_dirs"]
        item = get_args(ext_dirs.annotation)[0]

        assert ext_dirs.metadata == []
        assert FIJO_EN_RUNTIME in get_args(item)

    def test_la_marca_no_altera_la_validacion_de_la_ruta(self):
        # Un tipo marcado sigue anclando contra el home y expandiendo `~`.
        assert KnowledgeConfig(db_dirname="/abs/k").db_dirname == "/abs/k"
        assert WorkspaceConfig(path="/abs/ws").path == "/abs/ws"

    def test_un_annotated_sin_la_marca_no_la_trae(self):
        # Control negativo: la marca no se cuela en cualquier Annotated.
        assert FIJO_EN_RUNTIME not in get_args(Annotated[str, "otra-cosa"])


def _marcado(modelo, campo: str) -> bool:
    extra = modelo.model_fields[campo].json_schema_extra or {}
    return isinstance(extra, dict) and extra.get("fijo_en_runtime") is True


class TestMarcasEnLosCampos:
    def test_campos_de_agent_config_marcados(self):
        for campo in ("id", "system_prompt", "embedding", "providers", "channels"):
            assert _marcado(AgentConfig, campo), campo

    def test_campos_de_global_config_marcados(self):
        for campo in ("embedding", "providers", "channels", "admin"):
            assert _marcado(GlobalConfig, campo), campo

    def test_workspace_containment_marcado(self):
        assert _marcado(WorkspaceConfig, "containment")

    def test_campos_cambiables_no_llevan_la_marca(self):
        # Control negativo: la marca no es global ni se aplica por bloque entero.
        assert not _marcado(AgentConfig, "llm")
        assert not _marcado(AgentConfig, "name")
        assert not _marcado(GlobalConfig, "user")
        assert not _marcado(WorkspaceConfig, "path")  # fijo por TIPO, no por campo

    def test_la_marca_no_cambia_la_obligatoriedad_ni_el_default(self):
        assert AgentConfig.model_fields["id"].is_required()
        assert AgentConfig.model_fields["system_prompt"].default == ""
        assert GlobalConfig.model_fields["providers"].default == {}
        assert WorkspaceConfig().containment == "strict"

    def test_el_porque_de_cada_marca_llega_a_la_descripcion(self):
        # La docstring es la ayuda de config-reference.md: la marca sin motivo no sirve.
        assert "runtime" in (GlobalConfig.model_fields["admin"].description or "").lower()
        assert "runtime" in (WorkspaceConfig.model_fields["containment"].description or "").lower()


class TestPathsFijosEnRuntime:
    @pytest.fixture(scope="class")
    def fijos(self) -> frozenset[str]:
        return paths_fijos_en_runtime()

    def test_todo_secreto_es_fijo(self, fijos):
        secretos = paths_secretos()

        # Sanidad: el conjunto de secretos no está vacío, así que el loop corre de verdad.
        assert "providers.*.api_key" in secretos
        assert secretos <= fijos

    def test_los_secretos_de_canal_tambien(self, fijos):
        # `channels.telegram.token` sale de un modelo registrado por el canal.
        assert "channels.telegram.token" in paths_secretos()
        assert "channels.telegram.token" in fijos

    @pytest.mark.parametrize(
        "path",
        [
            "app.ext_dirs",  # list[RuntimePath]: la marca está en el tipo del ítem
            "workspace.path",  # ExpandedPath
            "memories.db_filename",  # RuntimePath
            "scheduler.db_filename",
            "knowledge.db_dirname",
            "knowledge.sources.path",  # ExpandedPath | None dentro de list[modelo]
            "embedding.model_dirname",
        ],
    )
    def test_los_campos_de_tipo_ruta_son_fijos(self, fijos, path):
        assert path in fijos

    @pytest.mark.parametrize(
        "path",
        [
            "id",
            "system_prompt",
            "workspace.containment",
            "embedding",
            "embedding.model",  # hijo de un campo marcado, sin marca propia
            "providers",
            "providers.*",
            "providers.*.base_url",
            "channels",
            "channels.*",
            "admin",
            "admin.host",
            "admin.port",
        ],
    )
    def test_los_campos_marcados_y_sus_hijos_son_fijos(self, fijos, path):
        assert path in fijos

    @pytest.mark.parametrize(
        "path",
        [
            "llm.model",
            "llm.temperature",
            "user.timezone",
            "memories.consolidation.enabled",
            "tools.pinned",
            "scheduler.enabled",
            "name",
            "workspace",  # el bloque entero NO es fijo: solo `path` y `containment`
        ],
    )
    def test_los_campos_cambiables_no_son_fijos(self, fijos, path):
        # El path existe en el schema: la ausencia se debe a la regla, no a un typo.
        assert path in {c.path for c in campos_del_schema()}
        assert path not in fijos

    def test_ratchet_por_nombre_un_campo_de_ruta_nunca_queda_sin_fijar(self, fijos):
        # Caza el campo de ruta tipado como `str` pelado: no lleva la marca del tipo,
        # pero por su NOMBRE es una ruta y tiene que estar en la denylist.
        sospechosos = [
            c.path
            for c in campos_del_schema()
            if (hoja := c.path.rsplit(".", 1)[-1]).endswith(("_filename", "_dirname", "_dirs"))
            or hoja == "path"
        ]

        # Sanidad: hay rutas que revisar (si el filtro no matchea nada, el test no prueba nada).
        assert len(sospechosos) >= 8
        sin_fijar = [p for p in sospechosos if p not in fijos]
        assert sin_fijar == []
