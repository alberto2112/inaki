"""Test de drift: la doc derivada del schema no puede quedar obsoleta.

Regenera cada artefacto desde el schema Pydantic y lo compara con el fichero
committeado. Si falla, el schema cambió y nadie regeneró la doc: correr
`inaki gen-docs`.

Cubre los dos artefactos generados:
  - `docs/config-reference.md`   — el drift `memory.*` que motivó el test.
  - `config/global.example.yaml` — se mantenía a mano y llegó a tener CINCO
    bloques del schema sin documentar (`scheduler`, `transcription`,
    `delegation`, `photos`, `knowledge`).
"""

from __future__ import annotations

from pathlib import Path

from infrastructure.config_docs import generate_config_reference, generate_global_example

_RAIZ = Path(__file__).resolve().parents[3]
_REFERENCE = _RAIZ / "docs" / "config-reference.md"
_EXAMPLE = _RAIZ / "config" / "global.example.yaml"


def test_config_reference_no_drift() -> None:
    generado = generate_config_reference()
    actual = _REFERENCE.read_text(encoding="utf-8")
    assert generado == actual, (
        "docs/config-reference.md está desincronizado del schema de config. "
        "Regeneralo con `inaki gen-docs`."
    )


def test_global_example_no_drift() -> None:
    generado = generate_global_example()
    actual = _EXAMPLE.read_text(encoding="utf-8")
    assert generado == actual, (
        "config/global.example.yaml está desincronizado del schema de config. "
        "Regeneralo con `inaki gen-docs`."
    )


def test_global_example_es_yaml_valido() -> None:
    """El ejemplo tiene que parsear: es lo primero que copia un operador."""
    import yaml

    from infrastructure.config import GlobalConfig

    datos = yaml.safe_load(_EXAMPLE.read_text(encoding="utf-8"))

    assert isinstance(datos, dict)
    faltantes = set(GlobalConfig.model_fields) - set(datos)
    assert not faltantes, f"bloques del schema ausentes del ejemplo: {sorted(faltantes)}"


def test_global_example_lo_acepta_el_propio_schema() -> None:
    """Que parsee como YAML no alcanza: el schema tiene que ACEPTARLO.

    Desde que la config falla ruidoso (`config-falla-ruidoso`), un ejemplo que
    el runtime rechazaría es peor que no tenerlo: el operador copia el bloque y
    el daemon no arranca. Este guard cazó `knowledge.sources` emitido como
    bloque anidado cuando el schema espera una lista.
    """
    import inspect
    from typing import get_args

    import yaml

    from infrastructure.config import GlobalConfig

    datos = yaml.safe_load(_EXAMPLE.read_text(encoding="utf-8"))
    rechazados: list[str] = []

    for nombre, valor in datos.items():
        field = GlobalConfig.model_fields.get(nombre)
        if field is None or not isinstance(valor, dict):
            continue
        modelo = next(
            (
                a
                for a in (field.annotation, *get_args(field.annotation))
                if inspect.isclass(a) and hasattr(a, "model_fields")
            ),
            None,
        )
        if modelo is None:
            continue
        try:
            modelo(**valor)
        except Exception as exc:  # noqa: BLE001 — cualquier rechazo cuenta
            rechazados.append(f"{nombre}: {str(exc).splitlines()[1].strip()}")

    assert not rechazados, "el ejemplo documenta config que el schema rechaza:\n" + "\n".join(
        rechazados
    )
