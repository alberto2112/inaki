"""Bloques ``delegation`` (global) y ``AgentDelegationConfig`` (per-agente).

Sección del schema de configuración. Solo declaraciones: sin I/O ni carga de YAML.
Importá desde ``inaki.config.schema`` (o ``inaki.config``).
"""

from __future__ import annotations

from inaki.config.schema._base import _ConfigBaseModel


class DelegationConfig(_ConfigBaseModel):
    """Lo que lleva una llamada delegada: presupuestos (iteraciones y timeout) y, en
    background, un directorio de trabajo propio.

    En ``global.yaml`` son los DEFAULTS; cada agente los hereda por el merge y
    los pisa en su propio bloque ``delegation`` (``AgentDelegationConfig``).
    Manda el valor del agente que DELEGA (el caller): el que delega fija el
    presupuesto y el directorio de la tarea. Declararlos en un sub-agente es un
    error de carga — el sub no delega, así que ahí no tendrían efecto."""

    max_iterations_per_sub: int = 10
    """Vueltas máximas del tool loop que puede gastar UNA llamada delegada.

    Equivalente de ``tools.tool_call_max_iterations`` para el turno one-shot del
    sub-agente, y con default más generoso (10 vs 5): al sub se le delega una
    tarea completa, no un intercambio conversacional. Lo fija el agente que
    delega: en ``global.yaml`` es el default, en ``agents/{id}.yaml`` lo pisa."""

    timeout_seconds: int = 60
    """Presupuesto de reloj de una llamada delegada, en segundos.

    Se aplica como ``asyncio.wait_for`` sobre el turno del sub-agente: al
    vencerse, la delegación se corta y el caller recibe el timeout como
    resultado. Es un techo de tiempo real, independiente de
    ``max_iterations_per_sub``, que cuenta vueltas. Vale igual para
    ``wait=true`` y para la delegación en background. Lo fija el agente que
    delega: en ``global.yaml`` es el default, en ``agents/{id}.yaml`` lo pisa."""

    grant_workdir: bool = False
    """Da a cada delegación EN BACKGROUND un directorio de trabajo propio dentro del workspace.

    Con ``True``, al encolar una delegación ``wait=false`` se crea
    ``<workspace.path del que delega>/dwss/<8 caracteres a-z0-9>``. El resultado
    ``queued`` de ``delegate`` le devuelve la ruta al agente, y el prompt del
    sub-agente le ordena crear ahí todo lo que produzca. Los ficheros de entrada se
    pasan con el parámetro ``input_files`` de ``delegate``, que los copia ahí ANTES
    de encolar (solo disponible con este flag; hasta 20 ficheros y 50 MiB en total,
    siempre dentro del workspace). Las delegaciones ``wait=true`` nunca reciben
    directorio.

    La contención es SOLO por instrucción: el sub-agente usa las tools del que
    delega, que resuelven contra su workspace entero, así que nada le impide
    escribir fuera del directorio. ``shell_exec`` no arranca en él.

    El directorio vive lo que vive el registro de la task: se borra cuando la
    retención poda la task (14 días o fuera de las últimas 20 terminadas por
    agente) y al arrancar se barren los que quedaron sin registro. No tiene tope de
    tamaño (los insumos sí). Lo fija el agente que delega: en ``global.yaml`` es el
    default, en ``agents/{id}.yaml`` lo pisa; declararlo en un sub-agente es error
    de carga."""


class AgentDelegationConfig(DelegationConfig):
    """Config de delegación por agente: si delega, a quién, y con qué presupuesto."""

    enabled: bool = False
    """Habilita la tool ``delegate`` para ESTE agente. Opt-in.

    Con ``False`` (default) la tool no se registra siquiera: no aparece en los
    schemas y el modelo no puede razonar sobre ella. Encenderlo también inyecta
    al system prompt el bloque de descubrimiento con los agentes disponibles y
    sus tools."""

    allowed_targets: list[str] = []
    """Allow-list de sub-agentes a los que delegar. Lista vacía = todos los disponibles.

    Se INTERSECA con los sub-agentes registrados (``agents/sub-agents/*.yaml``):
    nunca amplía el universo, solo lo recorta — un id que no existe se ignora.
    Filtra a la vez los destinos que la tool acepta y los que se anuncian en el
    bloque de descubrimiento del prompt. Si la intersección queda vacía, la tool
    ``delegate`` no se registra."""
