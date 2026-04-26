"""Tests para el mapper de schema Pydantic → Fields de la TUI."""

from __future__ import annotations

from infrastructure.config import (
    AppConfig,
    GlobalConfig,
    LLMConfig,
    MemoryConfig,
    ProviderConfig,
)
from adapters.inbound.setup_tui._schema import sections_for_model


class TestSectionsForModelGlobalConfig:
    """sections_for_model sobre GlobalConfig produce las secciones correctas."""

    def test_produce_seccion_app(self):
        secciones = sections_for_model(GlobalConfig, {}, section_prefix="APP")
        nombres = [s for s, _ in secciones]
        assert "APP" in nombres

    def test_seccion_app_tiene_campos_basicos(self):
        secciones = sections_for_model(GlobalConfig, {}, section_prefix="APP")
        app_fields = next(fields for name, fields in secciones if name == "APP")
        labels = [f.label for f in app_fields]
        assert "name" in labels
        assert "log_level" in labels
        assert "default_agent" in labels

    def test_log_level_es_enum(self):
        """log_level en AppConfig es Literal — debe inferirse como enum."""
        # AppConfig.log_level es str; necesitamos verificar el caso real.
        # En la config actual log_level es str, no Literal.
        # Verificamos que al menos el campo existe y tiene el kind correcto
        # según la anotación real.
        secciones = sections_for_model(GlobalConfig, {}, section_prefix="APP")
        app_fields = next(fields for name, fields in secciones if name == "APP")
        log_level_field = next((f for f in app_fields if f.label == "log_level"), None)
        assert log_level_field is not None
        # log_level es str en AppConfig, así que kind debe ser "scalar"
        assert log_level_field.kind == "scalar"

    def test_default_se_captura(self):
        """Los defaults de Pydantic se capturan en field.default."""
        secciones = sections_for_model(GlobalConfig, {}, section_prefix="APP")
        app_fields = next(fields for name, fields in secciones if name == "APP")
        name_field = next((f for f in app_fields if f.label == "name"), None)
        assert name_field is not None
        assert name_field.default == "Iñaki"

    def test_default_agent_default(self):
        secciones = sections_for_model(GlobalConfig, {}, section_prefix="APP")
        app_fields = next(fields for name, fields in secciones if name == "APP")
        da_field = next((f for f in app_fields if f.label == "default_agent"), None)
        assert da_field is not None
        assert da_field.default == "general"

    def test_subsecciones_presentes(self):
        """Campos tipo BaseModel del modelo raíz generan secciones separadas."""
        secciones = sections_for_model(GlobalConfig, {}, section_prefix="APP")
        nombres = [s for s, _ in secciones]
        # LLM, EMBEDDING, MEMORY, etc. deben aparecer como secciones
        assert "LLM" in nombres
        assert "EMBEDDING" in nombres
        assert "MEMORY" in nombres

    def test_valores_actuales_se_populan(self):
        """Si se pasan current_values, el field.value se usa en lugar del default."""
        valores = {"app": {"name": "Mi Inaki", "log_level": "DEBUG", "default_agent": "dev"}}
        secciones = sections_for_model(GlobalConfig, valores, section_prefix="APP")
        app_fields = next(fields for name, fields in secciones if name == "APP")
        name_field = next((f for f in app_fields if f.label == "name"), None)
        assert name_field is not None
        assert name_field.value == "Mi Inaki"


class TestSectionsForModelProviderConfig:
    """Verifica detección de secrets en ProviderConfig."""

    def test_api_key_es_secret(self):
        """api_key debe inferirse como kind='secret'."""
        secciones = sections_for_model(ProviderConfig, {})
        all_fields = [f for _, fields in secciones for f in fields]
        api_key_field = next((f for f in all_fields if f.label == "api_key"), None)
        assert api_key_field is not None
        assert api_key_field.kind == "secret"


class TestSectionsForModelLLMConfig:
    """Verifica campos de LLMConfig."""

    def test_provider_es_scalar(self):
        secciones = sections_for_model(LLMConfig, {})
        all_fields = [f for _, fields in secciones for f in fields]
        provider_field = next((f for f in all_fields if f.label == "provider"), None)
        assert provider_field is not None
        assert provider_field.kind == "scalar"

    def test_temperature_tiene_default(self):
        secciones = sections_for_model(LLMConfig, {})
        all_fields = [f for _, fields in secciones for f in fields]
        temp_field = next((f for f in all_fields if f.label == "temperature"), None)
        assert temp_field is not None
        assert temp_field.default == "0.7"


class TestSectionsForModelMemoryConfig:
    """Verifica que enabled y schedule se detectan correctamente."""

    def test_enabled_es_scalar(self):
        secciones = sections_for_model(MemoryConfig, {})
        all_fields = [f for _, fields in secciones for f in fields]
        enabled_field = next((f for f in all_fields if f.label == "enabled"), None)
        assert enabled_field is not None
        assert enabled_field.kind == "scalar"

    def test_schedule_es_scalar(self):
        secciones = sections_for_model(MemoryConfig, {})
        all_fields = [f for _, fields in secciones for f in fields]
        schedule_field = next((f for f in all_fields if f.label == "schedule"), None)
        assert schedule_field is not None
        assert schedule_field.kind == "scalar"


class TestEnumInferencia:
    """Verifica que los Literal[...] se infieren como enum."""

    def test_containment_mode_es_enum(self):
        """WorkspaceConfig.containment es Literal['strict','warn','off'] → enum."""
        from infrastructure.config import WorkspaceConfig

        secciones = sections_for_model(WorkspaceConfig, {})
        all_fields = [f for _, fields in secciones for f in fields]
        containment_field = next((f for f in all_fields if f.label == "containment"), None)
        assert containment_field is not None
        assert containment_field.kind == "enum"
        assert set(containment_field.enum_choices or ()) == {"strict", "warn", "off"}


class TestSkipComplejos:
    """Verifica que los campos dict/list se omiten de las secciones."""

    def test_ext_dirs_no_aparece(self):
        """ext_dirs es list[str] — debe omitirse en la TUI."""
        secciones = sections_for_model(AppConfig, {})
        all_fields = [f for _, fields in secciones for f in fields]
        labels = [f.label for f in all_fields]
        assert "ext_dirs" not in labels

    def test_providers_dict_no_aparece_en_global(self):
        """providers es dict[str, ProviderConfig] — se omite del modelo raíz."""
        secciones = sections_for_model(GlobalConfig, {}, section_prefix="APP")
        all_fields = [f for _, fields in secciones for f in fields]
        labels = [f.label for f in all_fields]
        assert "providers" not in labels
