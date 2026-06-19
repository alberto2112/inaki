"""SecretsPage — vista consolidada y PROACTIVA de los secrets.

A diferencia de la versión reactiva (que solo listaba lo ya escrito), recorre el
schema efectivo con ``iter_declared_secrets`` y muestra TODOS los secretos
declarados —marcados en el schema— separando los configurados de los PENDIENTES.
Así el operador ve de un vistazo qué falta por configurar, no solo lo que hay.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from textual.app import ComposeResult

from adapters.inbound.setup_tui._schema_tree import iter_declared_secrets
from adapters.inbound.setup_tui.domain.field import Field
from adapters.inbound.setup_tui.screens._base import BasePage
from adapters.inbound.setup_tui.widgets.config_row import ConfigRow
from adapters.inbound.setup_tui.widgets.section_header import SectionHeader
from core.ports.config_repository import LayerName
from core.use_cases.config._merge import deep_merge_con_eliminaciones

if TYPE_CHECKING:
    from adapters.inbound.setup_tui.di import SetupContainer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Re-exportado desde widgets/_masking.py para mantener la compatibilidad de tests
# y permitir que SecretsPage no dependa directamente del módulo de widgets.
from adapters.inbound.setup_tui.widgets._masking import mask_secret as _mask_secret  # noqa: E402, F401


def _unflatten(flat_key: str, value: Any) -> dict[str, Any]:
    """Convierte una clave punto-separada y un valor en un dict anidado.

    Ejemplo: ``_unflatten("providers.openai.api_key", "sk-x")``
    → ``{"providers": {"openai": {"api_key": "sk-x"}}}``
    """
    parts = flat_key.split(".")
    result: dict[str, Any] = {}
    current = result
    for i, part in enumerate(parts):
        if i == len(parts) - 1:
            current[part] = value
        else:
            current[part] = {}
            current = current[part]
    return result


# ---------------------------------------------------------------------------
# SecretsPage
# ---------------------------------------------------------------------------

# Tag que guardamos en _field_meta para routear el guardado al scope correcto.
# scope es "global" o "agent/{id}".
_FieldMeta = tuple[str, str]  # (scope, flat_key)


class SecretsPage(BasePage):
    """Página de vista consolidada de todos los secrets.

    Muestra secrets globales (``global.secrets.yaml``) y por agente
    (``agents/{id}.secrets.yaml``) en secciones separadas. Los valores
    se muestran enmascarados. Enter abre el modal de edición del secret.
    """

    def __init__(self, container: "SetupContainer | None", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._container = container
        # Mapeo field → (scope, flat_key)
        self._field_meta: dict[int, _FieldMeta] = {}

    def breadcrumb(self) -> str:
        return "inaki / config / secrets"

    def compose_body(self) -> ComposeResult:
        if self._container is None:
            yield SectionHeader("SECRETS")
            yield ConfigRow(Field(label="(sin container)", value="no disponible", kind="scalar"))
            return

        # ---- Global: config efectiva (global.yaml + global.secrets.yaml) ----
        try:
            global_eff = self._container.get_effective_config.execute().datos
        except Exception:
            global_eff = {}
        yield from self._emit_scope("GLOBAL", "global", self._container.global_schema, global_eff)

        # ---- Por agente: capa principal + secrets mergeadas ----
        try:
            agent_ids = self._container.list_agents.execute()
        except Exception:
            agent_ids = []

        for agent_id in agent_ids:
            try:
                main = self._container.repo.read_layer(LayerName.AGENT, agent_id=agent_id)
                secrets = self._container.repo.read_layer(
                    LayerName.AGENT_SECRETS, agent_id=agent_id
                )
                agent_eff = deep_merge_con_eliminaciones(main, secrets)
            except Exception:
                continue
            yield from self._emit_scope(
                f"AGENT/{agent_id.upper()}",
                f"agent/{agent_id}",
                self._container.agent_schema,
                agent_eff,
            )

    def _emit_scope(
        self, titulo: str, scope_tag: str, schema: Any, effective: dict[str, Any]
    ) -> ComposeResult:
        """Emite las filas de un scope: secretos configurados + sección de pendientes."""
        if self._container is None:
            return
        declared = iter_declared_secrets(
            schema, effective, channel_schemas=self._container.channel_schemas
        )
        configurados = [(p, v) for p, ok, v in declared if ok]
        pendientes = [p for p, ok, _ in declared if not ok]
        if not configurados and not pendientes:
            return

        yield SectionHeader(titulo)
        for path, raw_val in configurados:
            flat = ".".join(path)
            # Field guarda el valor REAL; el masking es solo display (ConfigRow).
            field = Field(label=flat, value=str(raw_val or ""), kind="secret")
            self._field_meta[id(field)] = (scope_tag, flat)
            yield ConfigRow(field)

        if pendientes:
            yield SectionHeader(f"{titulo} · PENDIENTES")
            for path in pendientes:
                flat = ".".join(path)
                field = Field(label=flat, value="", kind="secret")
                self._field_meta[id(field)] = (scope_tag, flat)
                yield ConfigRow(field)

    def _on_field_saved(self, field: Field) -> None:
        """Persiste el nuevo valor del secret en la capa correcta."""
        if self._container is None:
            return

        meta = self._field_meta.get(id(field))
        if meta is None:
            return

        scope, flat_key = meta

        from core.ports.config_repository import LayerName

        # field.value YA es el valor real — el modal lo recibe real y lo
        # devuelve real (el masking es solo de display en ConfigRow).
        real_value = field.value
        cambios = _unflatten(flat_key, real_value)

        try:
            if scope == "global":
                self._container.update_global_layer.execute(
                    cambios=cambios,
                    layer=LayerName.GLOBAL_SECRETS,
                )
            elif scope.startswith("agent/"):
                agent_id = scope[len("agent/") :]
                self._container.update_agent_layer.execute(
                    agent_id=agent_id,
                    cambios=cambios,
                    layer=LayerName.AGENT_SECRETS,
                )
            self.app.notify(
                f"guardado: {flat_key}",
                title="secrets",
                timeout=2,
            )
        except Exception as exc:
            self.app.notify(
                f"error al guardar {flat_key}: {exc}",
                title="error",
                severity="error",
                timeout=4,
            )

        # ConfigRow muestra el valor enmascarado a partir de field.value real.
        self._current_row().refresh_value()
