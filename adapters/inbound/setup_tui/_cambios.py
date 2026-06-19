"""Helper para construir el dict ``cambios`` respetando la jerarquía de
sub-secciones generada por el schema mapper.

El schema mapper emite secciones planas (``"MEMORIES"``) y anidadas
(``"MEMORIES.LLM"``). Al guardar, el dict de cambios debe reflejar esa
jerarquía para que el YAML escriba en la clave correcta:

  - ``MEMORIES`` + ``digest_size`` → ``{memories: {digest_size: value}}``
  - ``MEMORIES.LLM`` + ``provider`` → ``{memories: {llm: {provider: value}}}``
  - ``"AGENTCONFIG"`` + ``id`` (root field) → ``{id: value}``
"""

from __future__ import annotations

from typing import Any

from core.use_cases.config._merge import CampoTriestado, TristadoValor


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
            Puede ser plana (``"MEMORIES"``) o jerárquica (``"MEMORIES.LLM"``).
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


def cambios_anidados(path: tuple[str, ...], valor: Any) -> dict[str, Any]:
    """Construye el dict anidado ``{p0: {p1: {... : valor}}}`` desde un ``path``.

    Es la contraparte directa del árbol de schema (TUI v3): un ``SchemaNode``
    conoce su ``path`` de claves YAML reales, así que añadir o editar en ese path
    es envolver el valor en ese anidamiento. NO usa el mapa plano
    ``section_to_yaml`` — el path ya es el camino real en el YAML.

    Args:
        path: Claves desde la raíz, ej. ``("channels", "telegram", "groups")``.
        valor: Valor a colocar en la hoja (``{}`` para crear una sección vacía,
            el default del schema para un campo, o un ``CampoTriestado`` para borrar).

    Returns:
        El dict anidado listo para pasar como ``cambios`` al use case.

    Raises:
        ValueError: Si ``path`` está vacío (no hay clave que escribir).
    """
    if not path:
        raise ValueError("cambios_anidados requiere un path no vacío")
    inner: Any = valor
    for parte in reversed(path):
        inner = {parte: inner}
    return inner


def eliminar_en_path(path: tuple[str, ...]) -> dict[str, Any]:
    """Construye el ``cambios`` que ELIMINA la clave en ``path``.

    Reusa el marcador canónico de borrado del proyecto
    (``CampoTriestado(TristadoValor.INHERIT)``), que ambos use cases
    (``UpdateAgentLayerUseCase`` y ``UpdateGlobalLayerUseCase``) resuelven al
    sentinel de eliminación durante el merge. Sirve tanto para borrar una
    sección entera como un campo individual — el merge poda la clave terminal
    y deja intacto el resto del árbol.

    Args:
        path: Claves desde la raíz hasta la sección/campo a eliminar.

    Returns:
        El dict ``cambios`` anidado terminando en el marcador de borrado.
    """
    return cambios_anidados(path, CampoTriestado(TristadoValor.INHERIT))
