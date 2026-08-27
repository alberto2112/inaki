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

import inspect
import re
from pathlib import Path

from pydantic import BaseModel

from infrastructure.config_docs import generate_config_reference, generate_global_example
from infrastructure.config_schema import CHANNEL_SCHEMAS, AgentConfig, GlobalConfig

_RAIZ = Path(__file__).resolve().parents[3]
_REFERENCE = _RAIZ / "docs" / "config-reference.md"
_EXAMPLE = _RAIZ / "config" / "global.example.yaml"


def _aplanar(texto: str) -> str:
    """Colapsa todo el whitespace: compara CONTENIDO, no envoltura de línea."""
    return re.sub(r"\s+", " ", texto).strip()


def _modelos_del_schema() -> list[type[BaseModel]]:
    """Todos los modelos alcanzables desde las raíces, incluidos los canales."""
    vistos: list[type[BaseModel]] = []

    def recorrer(modelo: type[BaseModel]) -> None:
        if modelo in vistos:
            return
        vistos.append(modelo)
        for field in modelo.model_fields.values():
            anotacion = field.annotation
            candidatos = getattr(anotacion, "__args__", ()) or (anotacion,)
            for sub in candidatos:
                if inspect.isclass(sub) and issubclass(sub, BaseModel):
                    recorrer(sub)

    raices: list[type[BaseModel]] = [GlobalConfig, AgentConfig, *CHANNEL_SCHEMAS.values()]
    for raiz in raices:
        recorrer(raiz)
    return vistos


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


def test_config_reference_publica_los_docstrings_ENTEROS() -> None:
    """Nada de lo escrito en el schema puede quedarse sin publicar.

    El test de drift de arriba NO cubre esto: compara byte a byte contra el
    fichero, así que un renderer que vuelva a recortar al primer párrafo pasa
    limpio en cuanto alguien corre `inaki gen-docs`. El agujero es silencioso y
    ya ocurrió — la referencia publicaba 12.800 de 44.500 caracteres, y lo que
    faltaba era la lista de adapters de `llm.provider`: el operador que quería
    declarar un provider a mano no tenía dónde leer qué valores existen.

    El invariante "documentá el parámetro en su docstring del schema" solo se
    sostiene si ese docstring LLEGA. Si no llega, empuja a escribir la doc en
    otro lado — que es exactamente lo que el invariante prohíbe.
    """
    generado = _aplanar(generate_config_reference())
    faltantes: list[str] = []

    for modelo in _modelos_del_schema():
        fuentes = [("(docstring de clase)", inspect.getdoc(modelo))]
        fuentes += [(nombre, f.description) for nombre, f in modelo.model_fields.items()]
        for nombre, texto in fuentes:
            if not texto:
                continue
            for parrafo in re.split(r"\n\s*\n", inspect.cleandoc(texto)):
                aplanado = _aplanar(parrafo)
                if aplanado and aplanado not in generado:
                    faltantes.append(f"{modelo.__name__}.{nombre}: {aplanado[:90]}…")

    assert not faltantes, (
        "docs/config-reference.md no publica todo lo escrito en el schema — "
        "el renderer está recortando:\n  " + "\n  ".join(faltantes)
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
