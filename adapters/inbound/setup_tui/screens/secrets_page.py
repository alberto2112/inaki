"""SecretsPage — vista consolidada de todos los secrets (global + por agente)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from textual.app import ComposeResult

from adapters.inbound.setup_tui.domain.field import Field
from adapters.inbound.setup_tui.screens._base import BasePage
from adapters.inbound.setup_tui.widgets.config_row import ConfigRow
from adapters.inbound.setup_tui.widgets.section_header import SectionHeader

if TYPE_CHECKING:
    from adapters.inbound.setup_tui.di import SetupContainer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Re-exportado desde widgets/_masking.py para mantener la compatibilidad de tests
# y permitir que SecretsPage no dependa directamente del módulo de widgets.
from adapters.inbound.setup_tui.widgets._masking import mask_secret as _mask_secret  # noqa: E402, F401


def _flatten(data: dict[str, Any], prefix: str = "") -> list[tuple[str, Any]]:
    """Aplana un dict anidado en lista de ``(clave_punto, valor)``.

    Ejemplo: ``{"providers": {"openai": {"api_key": "sk-x"}}}``
    → ``[("providers.openai.api_key", "sk-x")]``

    Solo aplana hasta el nivel donde el valor ya NO es dict.
    """
    items: list[tuple[str, Any]] = []
    for key, val in data.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(val, dict):
            items.extend(_flatten(val, full_key))
        else:
            items.append((full_key, val))
    return items


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
            yield ConfigRow(
                Field(label="(sin container)", value="no disponible", kind="scalar")
            )
            return

        from textual.widgets import Label

        from core.ports.config_repository import LayerName

        # ---- Secrets globales ----
        yield SectionHeader("GLOBAL SECRETS")
        global_secrets: dict[str, Any] = {}
        try:
            global_secrets = self._container.repo.read_layer(LayerName.GLOBAL_SECRETS)
        except Exception as exc:
            yield Label(
                f"  [red]error al leer global.secrets.yaml: {type(exc).__name__}: {exc}[/red]",
                markup=True,
            )

        items_global = _flatten(global_secrets)
        if items_global:
            for flat_key, raw_val in items_global:
                raw_str = str(raw_val) if raw_val is not None else ""
                # Field guarda el valor REAL — el masking es solo display
                # (lo aplica ConfigRow). Esto evita que el modal reciba la
                # versión enmascarada y la persista al guardar.
                field = Field(label=flat_key, value=raw_str, kind="secret")
                self._field_meta[id(field)] = ("global", flat_key)
                yield ConfigRow(field)
        else:
            yield ConfigRow(
                Field(label="(vacío)", value="sin secrets globales configurados", kind="scalar")
            )

        # ---- Secrets por agente ----
        try:
            agent_ids = self._container.list_agents.execute()
        except Exception:
            agent_ids = []

        for agent_id in agent_ids:
            agent_error: str | None = None
            agent_secrets: dict[str, Any] = {}
            try:
                agent_secrets = self._container.repo.read_layer(
                    LayerName.AGENT_SECRETS, agent_id=agent_id
                )
            except Exception as exc:
                agent_error = f"{type(exc).__name__}: {exc}"

            items_agent = _flatten(agent_secrets)
            if not items_agent and agent_error is None:
                continue

            yield SectionHeader(f"AGENT/{agent_id.upper()}")
            if agent_error is not None:
                yield Label(
                    f"  [red]error al leer secrets de '{agent_id}': {agent_error}[/red]",
                    markup=True,
                )
                continue
            for flat_key, raw_val in items_agent:
                raw_str = str(raw_val) if raw_val is not None else ""
                # Field guarda el valor REAL — masking solo en display.
                field = Field(label=flat_key, value=raw_str, kind="secret")
                self._field_meta[id(field)] = (f"agent/{agent_id}", flat_key)
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
                agent_id = scope[len("agent/"):]
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
