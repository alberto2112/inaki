"""
config_cli — inspección de la configuración efectiva.

Sub-app de Typer:
  config show [--agent ID] [--origin] [--json] [--secrets]

Responde "¿qué config está viendo el runtime, y de dónde sale cada valor?" sin
tener que abrir los YAML y hacer el merge a mano. Los secretos SIEMPRE salen
redactados: el output está pensado para pegarse en un issue.

Composition root: acá es legítimo importar de ``infrastructure`` lo que el use
case necesita (los defaults del schema y qué campos son credenciales), para que
``core/`` no lo conozca. Esa introspección vive en
``infrastructure/config_introspection.py`` — la comparte con el container, que
arma la misma vista para la tool ``config`` del LLM.
"""

from __future__ import annotations

import json as json_lib
from typing import Optional

import typer

config_app = typer.Typer(help="Inspeccionar la configuración efectiva.")


def _construir_use_case():
    from adapters.outbound.config_repository import YamlRepository
    from core.use_cases.config.show_effective import ShowEffectiveConfigUseCase
    from infrastructure.config_introspection import defaults_del_schema, paths_secretos
    from infrastructure.home import get_inaki_home

    home = get_inaki_home()
    return ShowEffectiveConfigUseCase(
        repo=YamlRepository(config_dir=home / "config", agents_dir=home / "agents"),
        defaults=defaults_del_schema(),
        paths_secretos=paths_secretos(),
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
    from infrastructure.config import AgentRegistry, ensure_user_config, load_global_config
    from infrastructure.home import get_inaki_home

    from inaki.config_errors import borde_de_config

    home = get_inaki_home()
    config_dir, agents_dir = home / "config", home / "agents"

    # `show` promete "la que ve el runtime, no la que está escrita" — y sin este
    # paso mentía. El use case mergea los YAML CRUDOS contra los defaults del
    # schema y NO valida: una config que aborta el arranque salía por acá con
    # exit 0, listando un `llm.api_key` legacy como credencial configurada y un
    # `schedulr:` como campo válido. El operador leía el OK de la única
    # herramienta de diagnóstico que tiene, mientras el daemon se negaba a
    # levantar por ese mismísimo campo. Una herramienta que no puede decir "esto
    # no carga" es peor que no tenerla.
    #
    # Se valida con el MISMO loader del arranque (global + todos los agentes +
    # unicidad de canal): así `show` no puede discrepar del daemon por
    # construcción. Que aborte por un agente distinto del consultado es
    # deliberado — el daemon tampoco levanta a medias.
    with borde_de_config(str(home)):
        ensure_user_config(config_dir, agents_dir)
        _, global_raw = load_global_config(config_dir)
        AgentRegistry(agents_dir, global_raw)

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
