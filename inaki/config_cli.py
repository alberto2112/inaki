"""
config_cli — inspección de la configuración efectiva.

Sub-app de Typer:
  config show [--agent ID] [--origin] [--json] [--secrets]

Responde "¿qué config está viendo el runtime, y de dónde sale cada valor?" sin
tener que abrir los YAML y hacer el merge a mano. Los secretos SIEMPRE salen
redactados: el output está pensado para pegarse en un issue.

Composition root: acá es legítimo importar el schema de ``infrastructure`` y
derivar de él lo que el use case necesita (los defaults y qué campos son
credenciales), para que ``core/`` no lo conozca.
"""

from __future__ import annotations

import inspect
import json as json_lib
from typing import Any, Optional

import typer
from pydantic import BaseModel

config_app = typer.Typer(help="Inspeccionar la configuración efectiva.")


def _defaults_del_schema() -> dict[str, Any]:
    """Config que el schema aplica cuando nadie declara nada.

    Es la capa base del dump: sin ella, ``config show`` solo mostraría lo
    escrito en los YAML y no lo que el runtime realmente usa.

    Los bloques marcados como requeridos (``llm``, ``memories``, …) también
    entran: el loader los materializa SIEMPRE con ``Modelo(**merged.get(x, {}))``,
    así que su "obligatoriedad" es estructural y no llega nunca al operador.
    Omitirlos dejaría fuera del dump justamente los campos más consultados.
    """
    from infrastructure.config import AgentConfig, GlobalConfig

    defaults: dict[str, Any] = {}
    for modelo in (GlobalConfig, AgentConfig):
        for nombre, field in modelo.model_fields.items():
            if nombre in defaults:
                continue
            valor = _default_de_campo(field)
            if valor is not None:
                defaults[nombre] = valor
    return defaults


def _default_de_campo(field: Any) -> Any:
    """Default efectivo de un campo, o ``None`` si no tiene uno representable."""
    if not field.is_required():
        valor = field.get_default(call_default_factory=True)
        return valor.model_dump() if isinstance(valor, BaseModel) else valor

    # Requerido: si su tipo es un modelo instanciable sin argumentos, el loader
    # lo construye así. Un requerido escalar (``id``, ``name``) no tiene default.
    anotacion = field.annotation
    if inspect.isclass(anotacion) and issubclass(anotacion, BaseModel):
        try:
            return anotacion().model_dump()
        except Exception:
            return None
    return None


def _paths_secretos() -> frozenset[str]:
    """Rutas punteadas de los campos marcados como credenciales en el schema.

    Usa ``*`` para los dicts indexados por nombre (``providers.*.api_key``,
    ``channels.*.token``): esas claves las pone el operador, no el schema.
    """
    from infrastructure.config import CHANNEL_SCHEMAS, AgentConfig, GlobalConfig

    encontrados: set[str] = set()

    def _recorrer(modelo: type[BaseModel], prefijo: str, vistos: set[type]) -> None:
        if modelo in vistos:
            return
        vistos.add(modelo)
        for nombre, field in modelo.model_fields.items():
            extra = field.json_schema_extra or {}
            if isinstance(extra, dict) and extra.get("secret"):
                encontrados.add(f"{prefijo}{nombre}")
            for sub in _submodelos(field.annotation):
                _recorrer(sub, f"{prefijo}{nombre}.", vistos)

    def _submodelos(annotation: Any) -> list[type[BaseModel]]:
        from typing import get_args, get_origin

        # Un dict indexado (``providers: dict[str, ProviderConfig]``) NO se
        # recorre acá: su path real lleva una clave que pone el operador, así
        # que se cubre aparte con comodín. Descender daría ``providers.api_key``,
        # un path que no existe en ningún YAML.
        if get_origin(annotation) is dict:
            return []
        candidatos = [annotation, *get_args(annotation)]
        return [c for c in candidatos if inspect.isclass(c) and issubclass(c, BaseModel)]

    vistos: set[type] = set()
    for raiz in (GlobalConfig, AgentConfig):
        _recorrer(raiz, "", vistos)

    # `providers` y `channels` son dicts indexados por una clave del operador:
    # la recursión por anotaciones no puede nombrarlas, así que van con comodín.
    from infrastructure.config import ProviderConfig

    for nombre, field in ProviderConfig.model_fields.items():
        extra = field.json_schema_extra or {}
        if isinstance(extra, dict) and extra.get("secret"):
            encontrados.add(f"providers.*.{nombre}")

    for canal, modelo in CHANNEL_SCHEMAS.items():
        _recolectar_secretos_anidados(modelo, f"channels.{canal}.", encontrados)

    return frozenset(encontrados)


def _recolectar_secretos_anidados(
    modelo: type[BaseModel], prefijo: str, destino: set[str], vistos: set[type] | None = None
) -> None:
    """Igual que el recorrido de arriba pero arrancando en un modelo de canal."""
    from typing import get_args

    vistos = vistos if vistos is not None else set()
    if modelo in vistos:
        return
    vistos.add(modelo)
    for nombre, field in modelo.model_fields.items():
        extra = field.json_schema_extra or {}
        if isinstance(extra, dict) and extra.get("secret"):
            destino.add(f"{prefijo}{nombre}")
        for candidato in (field.annotation, *get_args(field.annotation)):
            if inspect.isclass(candidato) and issubclass(candidato, BaseModel):
                _recolectar_secretos_anidados(candidato, f"{prefijo}{nombre}.", destino, vistos)


def _construir_use_case():
    from adapters.outbound.config_repository import YamlRepository
    from core.use_cases.config.show_effective import ShowEffectiveConfigUseCase
    from infrastructure.home import get_inaki_home

    home = get_inaki_home()
    return ShowEffectiveConfigUseCase(
        repo=YamlRepository(config_dir=home / "config", agents_dir=home / "agents"),
        defaults=_defaults_del_schema(),
        paths_secretos=_paths_secretos(),
    )


@config_app.command("show")
def show(
    agent: Optional[str] = typer.Option(
        None, "--agent", "-a", help="Agente cuya config efectiva se muestra."
    ),
    origin: bool = typer.Option(
        False, "--origin", help="Anota de qué capa sale cada valor (default/global/agent)."
    ),
    json_output: bool = typer.Option(False, "--json", help="Salida JSON."),
    secrets: bool = typer.Option(
        False, "--secrets", help="Solo las credenciales: cuáles están puestas y cuáles faltan."
    ),
) -> None:
    """Muestra la config efectiva — la que ve el runtime, no la que está escrita.

    Los valores secretos salen siempre redactados: el output se puede pegar en
    un issue sin filtrar credenciales.
    """
    from infrastructure.config import ensure_user_config
    from infrastructure.home import get_inaki_home

    ensure_user_config(get_inaki_home() / "config", get_inaki_home() / "agents")

    from core.domain.errors import AgentNotFoundError

    try:
        vista = _construir_use_case().execute(agent)
    except AgentNotFoundError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    campos = vista.secretos() if secrets else vista.campos

    if json_output:
        typer.echo(
            json_lib.dumps(
                {
                    "agent": vista.agent_id,
                    "campos": [
                        {
                            "path": c.path,
                            "valor": c.valor,
                            "origen": c.origen,
                            "secreto": c.es_secreto,
                            "configurado": c.configurado,
                        }
                        for c in campos
                    ],
                },
                indent=2,
                ensure_ascii=False,
                default=str,
            )
        )
        return

    if not campos:
        typer.echo("(sin campos)" if not secrets else "(el schema no declara credenciales)")
        return

    ancho = max(len(c.path) for c in campos)
    for c in campos:
        marca = "🔒 " if c.es_secreto else "   "
        linea = f"{marca}{c.path.ljust(ancho)}  {c.valor}"
        if origin:
            linea += f"   [{c.origen}]"
        typer.echo(linea)
