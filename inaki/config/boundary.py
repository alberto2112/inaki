"""Borde del composition root para los errores de configuración.

``config-falla-ruidoso`` prometió que una config rota nombra el fichero, el
bloque y la clave a corregir. **El dominio cumple**: los ``ConfigError`` traen el
path, la clave sobrante, la sugerencia de ``difflib`` y hasta un ejemplo del
shape nuevo. Lo que faltaba era el BORDE.

Sin handler, ese mensaje sale enterrado bajo treinta frames de traceback: el
operador lee Python en vez de leer su error, y una promesa cumplida en el core se
pierde en el último metro. Es la misma forma que ``formato-en-el-borde-del-
transporte``, pero del lado de entrada — y la lección es idéntica: **el borde es
UNO**, no un ``try`` por call-site.

Este módulo es ese borde. Vive en ``inaki/`` porque ensamblar es su trabajo, y es
un módulo propio (y no una función en ``cli.py``) porque ``cli.py`` importa
``config_cli`` y ambos lo necesitan.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from typing import Iterator

# ``EX_CONFIG`` de sysexits.h: "la config está mal", no "el proceso se cayó". La unidad
# systemd lo lista en ``RestartPreventExitStatus``: con exit 1 ``Restart=on-failure``
# reintentaba cada 5 s para siempre una config que ningún reinicio va a arreglar.
EXIT_CONFIG = 78
# El fallo de config de un RELOAD sí lo puede arreglar un reinicio: el proceso puede traer
# overrides en memoria (``config set``) que el disco no tiene, y systemd reinicia leyendo
# disco (``reload-fallido-reinicia``). Si el disco también está roto, el arranque limpio
# que sigue sale con ``EXIT_CONFIG`` y ahí se para.
EXIT_REINICIAR = 1


def errores_del_operador() -> tuple[type[BaseException], ...]:
    """Familias de error que significan "tu config está mal", no "hay un bug".

    Import diferido: este módulo lo carga el arranque, antes de que valga la pena
    pagar pydantic y ruamel.

    - ``ConfigError``     — el dominio ya redactó el mensaje accionable.
    - ``ValidationError`` — el schema rechazó un valor o una clave desconocida.
    - ``YAMLError``       — el fichero ni siquiera parsea (indentación, clave
      duplicada). Las DOS libs: el loader usa PyYAML y las migraciones ruamel,
      y sus excepciones no comparten jerarquía.
    - ``OSError``         — no se puede leer el fichero. Desde que
      ``secrets-layer-eradication`` puso 600 en TODAS las capas, un daemon
      corriendo con otro usuario es un fallo esperable, no un bug.
    """
    import yaml
    from pydantic import ValidationError
    from ruamel.yaml import YAMLError as RuamelYAMLError

    from inaki.shared.errors import ConfigError

    return (ConfigError, ValidationError, yaml.YAMLError, RuamelYAMLError, OSError)


@contextmanager
def borde_de_config(
    contexto: str | None = None, *, codigo_de_salida: int = EXIT_CONFIG
) -> Iterator[None]:
    """Convierte un fallo de config en un mensaje limpio + exit ``codigo_de_salida``.

    ``EXIT_CONFIG`` por defecto; el re-bootstrap de un reload pasa ``EXIT_REINICIAR``.

    ``contexto`` es el directorio o fichero que se estaba leyendo, y solo se
    imprime si el error no lo dijo ya: los ``ConfigError`` de un agente traen el
    path absoluto adentro, y repetirlo entero delante hace ruido justo donde el
    operador tiene que leer con atención. Los del global no lo traen (un shape
    legacy o un ``ValidationError`` de pydantic no nombran fichero) y con
    ``--home`` / ``INAKI_HOME`` en juego "qué instancia falló" no es deducible:
    para esos existe.

    Lo que NO es config del operador (un ``AttributeError`` nuestro, por ejemplo)
    sigue saliendo como traceback: ahí el traceback ES la información, y
    disfrazarlo de "revisá tu YAML" mandaría al operador a buscar un typo que no
    existe.
    """
    try:
        yield
    except errores_del_operador() as exc:
        detalle = f" en {contexto}" if contexto and contexto not in str(exc) else ""
        print(f"Error de configuración{detalle}: {exc}", file=sys.stderr)
        sys.exit(codigo_de_salida)
