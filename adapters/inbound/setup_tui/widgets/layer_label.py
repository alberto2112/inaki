"""
LayerLabel — badge que muestra el origen de un campo de configuración.

Indica en qué capa YAML se definió el valor actual del campo:
  - "from: global.yaml"
  - "from: global.secrets.yaml"
  - "from: agents/{id}.yaml"
  - "from: agents/{id}.secrets.yaml"

Útil en formularios de edición para que el usuario entienda qué archivo
se va a modificar si cambia el campo.
"""

from __future__ import annotations

from textual.widgets import Label

# Mapa de nombre de capa → etiqueta amigable
_ETIQUETAS: dict[str, str] = {
    "global": "global.yaml",
    "global.secrets": "global.secrets.yaml",
    "agent": "agents/{id}.yaml",
    "agent.secrets": "agents/{id}.secrets.yaml",
}

_COLOR_POR_CAPA: dict[str, str] = {
    "global": "blue",
    "global.secrets": "red",
    "agent": "green",
    "agent.secrets": "dark_red",
}


class LayerLabel(Label):
    """
    Badge pequeño que muestra el origen de un campo de configuración.

    Args:
        capa: Nombre de la capa según ``OrigenCampo.capa``
              (``"global"``, ``"global.secrets"``, ``"agent"``, ``"agent.secrets"``).
        agent_id: Si la capa es de agente, se interpola en la etiqueta.
    """

    DEFAULT_CSS = """
    LayerLabel {
        margin-left: 1;
        color: $text-muted;
        text-style: italic;
    }
    """

    def __init__(
        self,
        capa: str,
        agent_id: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        etiqueta = _etiqueta_para(capa, agent_id)
        color = _COLOR_POR_CAPA.get(capa, "white")
        texto = f"[{color}]from: {etiqueta}[/{color}]"
        super().__init__(texto, id=id, classes=classes, markup=True)
        self._capa = capa

    def actualizar_capa(self, capa: str, agent_id: str | None = None) -> None:
        """Actualiza el badge para reflejar una nueva capa de origen."""
        etiqueta = _etiqueta_para(capa, agent_id)
        color = _COLOR_POR_CAPA.get(capa, "white")
        self.update(f"[{color}]from: {etiqueta}[/{color}]")
        self._capa = capa


def _etiqueta_para(capa: str, agent_id: str | None) -> str:
    """Retorna la etiqueta legible para la capa dada."""
    base = _ETIQUETAS.get(capa, capa)
    if agent_id and "{id}" in base:
        return base.replace("{id}", agent_id)
    return base
