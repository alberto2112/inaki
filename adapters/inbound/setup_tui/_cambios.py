"""Helper para construir el dict ``cambios`` respetando la jerarquía de
sub-secciones generada por el schema mapper.

El schema mapper emite secciones planas (``"MEMORY"``) y anidadas
(``"MEMORY.LLM"``). Al guardar, el dict de cambios debe reflejar esa
jerarquía para que el YAML escriba en la clave correcta:

  - ``MEMORY`` + ``provider`` → ``{memory: {provider: value}}``
  - ``MEMORY.LLM`` + ``provider`` → ``{memory: {llm: {provider: value}}}``
  - ``"AGENTCONFIG"`` + ``id`` (root field) → ``{id: value}``
"""

from __future__ import annotations

from typing import Any


def build_cambios(
    section_name: str,
    field_name: str,
    value: Any,
    section_to_yaml: dict[str, str],
    root_fields: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Construye el dict ``cambios`` para los use cases ``update_*_layer``.

    Args:
        section_name: Nombre de la sección que emitió el schema mapper.
            Puede ser plana (``"MEMORY"``) o jerárquica (``"MEMORY.LLM"``).
        field_name: Nombre del campo dentro de la sección.
        value: Valor a persistir.
        section_to_yaml: Mapa ``SECCION_RAÍZ_UPPER → clave_yaml`` específico
            de la página (Global o Agent).
        root_fields: Campos que viven directo en el root del YAML (sin
            contenedor de sección). Típicamente ``id, name, description,
            system_prompt`` para AgentConfig.

    Returns:
        Dict listo para pasar como ``cambios=`` al use case correspondiente.
    """
    if field_name in root_fields:
        return {field_name: value}

    parts = section_name.split(".")
    top = section_to_yaml.get(parts[0].upper(), parts[0].lower())

    inner: Any = {field_name: value}
    for part in reversed(parts[1:]):
        inner = {part.lower(): inner}

    return {top: inner}
