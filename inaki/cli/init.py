"""``inaki init`` — el primer arranque, en preguntas secuenciales.

Sin TUI: ``typer.prompt``/``typer.confirm`` uno detrás de otro, y Rich solo
para la barra de descarga del modelo. Escribe por los MISMOS use cases que
cualquier otra edición de config (``UpsertProvider``, ``UpdateGlobalLayer``,
``CreateAgent``) sobre el ``YamlRepository``, y al final valida con el MISMO
loader del arranque: si lo que escribió no carga, lo dice acá y no cuando el
daemon se niega a levantar.

Idempotente a propósito: sobre un home ya configurado pregunta antes de pisar
credenciales y antes de crear otro agente; nunca borra nada.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from inaki.cli import _common
from inaki.config.home import get_inaki_home


def _repo():
    from inaki.config.adapters.yaml_repository import YamlRepository

    return YamlRepository(config_dir=_common.get_config_dir(), agents_dir=_common.get_agents_dir())


def _paso(numero: int, titulo: str) -> None:
    typer.secho(f"\n[{numero}] {titulo}", bold=True)


def init(
    no_model: bool = typer.Option(
        False, "--no-model", help="No ofrecer la descarga del modelo local de embeddings."
    ),
) -> None:
    """Configura una instalación nueva: provider, primer agente, Telegram y modelo de embeddings."""
    from inaki.config import AgentRegistry, load_global_config
    from inaki.config.boundary import borde_de_config
    from inaki.config.ports import LayerName
    from inaki.config.schema.llm import LLMConfig
    from inaki.config.use_cases.create_agent import CreateAgentUseCase
    from inaki.config.use_cases.update_global_layer import UpdateGlobalLayerUseCase
    from inaki.config.use_cases.upsert_provider import UpsertProviderUseCase
    from inaki.llm.wiring import LLMProviderFactory

    home = get_inaki_home()
    config_dir, agents_dir = _common.resolve_dirs()  # crea el layout y global.yaml si faltan
    repo = _repo()
    typer.echo(f"Inaki — configuración inicial en {home}")

    # --- [1] provider LLM --------------------------------------------------
    _paso(1, "Proveedor de LLM")
    disponibles = LLMProviderFactory.available()
    llm_default = LLMConfig()
    typer.echo("Adapters disponibles: " + ", ".join(disponibles))
    provider = typer.prompt("Provider", default=llm_default.provider)
    if provider not in disponibles:
        typer.secho(f"'{provider}' no es un adapter conocido.", fg="red", err=True)
        raise typer.Exit(code=1)

    existentes: dict = (repo.read_layer(LayerName.GLOBAL).get("providers") or {}).get(
        provider
    ) or {}
    api_key: Optional[str] = None
    if existentes.get("api_key") and not typer.confirm(
        f"'{provider}' ya tiene api_key configurada. ¿Reemplazarla?", default=False
    ):
        typer.echo("  se conserva la credencial existente")
    else:
        api_key = typer.prompt(
            "API key (vacío si el provider no la necesita)",
            default="",
            hide_input=True,
            show_default=False,
        )
    base_url = typer.prompt(
        "Base URL (Enter para el default del adapter)", default="", show_default=False
    )
    modelo = typer.prompt(
        "Modelo", default=llm_default.model if provider == llm_default.provider else None
    )

    UpsertProviderUseCase(repo).execute(
        provider, api_key=api_key or None, base_url=base_url or None
    )

    # --- [2] primer agente -------------------------------------------------
    _paso(2, "Primer agente")
    agentes = repo.list_agents()
    crear_agente = True
    if agentes:
        typer.echo("Agentes existentes: " + ", ".join(agentes))
        crear_agente = typer.confirm("¿Crear otro agente?", default=False)
    agent_id = agentes[0] if agentes else "general"
    if crear_agente:
        agent_id = typer.prompt("Id (slug, sin espacios)", default="general")
        if agent_id in agentes:
            typer.secho(f"El agente '{agent_id}' ya existe.", fg="red", err=True)
            raise typer.Exit(code=1)
        nombre = typer.prompt("Nombre", default="Inaki")
        descripcion = typer.prompt("Descripción", default="Asistente personal")
        system_prompt = typer.prompt(
            "System prompt (una línea; después lo editás en el YAML)",
            default=f"Sos {nombre}, un asistente personal.",
        )

        # --- [3] Telegram (opcional) ----------------------------------------
        _paso(3, "Telegram (opcional)")
        extra: dict = {}
        if typer.confirm("¿Conectar este agente a Telegram?", default=False):
            token = typer.prompt("Token del bot (BotFather)", hide_input=True)
            ids = typer.prompt(
                "IDs de usuario permitidos, separados por coma (vacío = todos)",
                default="",
                show_default=False,
            )
            allowed = [s.strip() for s in ids.split(",") if s.strip()]
            extra["channels"] = {"telegram": {"token": token, "allowed_user_ids": allowed}}

        CreateAgentUseCase(repo).execute(
            agent_id,
            nombre,
            descripcion=descripcion,
            system_prompt=system_prompt,
            template_extra=extra or None,
        )
        typer.echo(f"  agente '{agent_id}' creado en {agents_dir / (agent_id + '.yaml')}")
    else:
        _paso(3, "Telegram (opcional)")
        typer.echo("  sin agente nuevo, no hay nada que conectar")

    UpdateGlobalLayerUseCase(repo).execute(
        {"llm": {"provider": provider, "model": modelo}, "app": {"default_agent": agent_id}}
    )

    # --- validar con el loader del arranque ---------------------------------
    with borde_de_config(str(home)):
        global_cfg, global_raw = load_global_config(config_dir)
        AgentRegistry(agents_dir, global_raw)
    typer.secho("\n✓ Configuración válida.", fg="green")

    # --- [4] modelo de embeddings -------------------------------------------
    if not no_model and global_cfg.embedding.provider == "e5_onnx":
        _paso(4, "Modelo local de embeddings")
        _descargar_modelo(Path(global_cfg.embedding.model_dirname))

    typer.echo(
        "\nListo. Probá con `inaki` (chat) o instalá el servicio con `inaki service install`."
    )


def _descargar_modelo(dest: Path) -> None:
    from inaki.embedding.download import (
        MODELO_E5_REPO,
        TAMANIO_APROX_MB,
        descargar_modelo_e5,
        modelo_e5_completo,
    )

    if modelo_e5_completo(dest):
        typer.echo(f"  modelo ya presente en {dest}")
        return
    if not typer.confirm(
        f"¿Descargar {MODELO_E5_REPO} (≈{TAMANIO_APROX_MB} MB) a {dest}?", default=True
    ):
        typer.echo("  sin modelo, la memoria semántica no arranca; volvé a correr `inaki init`")
        return

    import httpx
    from rich.progress import (
        BarColumn,
        DownloadColumn,
        Progress,
        TaskID,
        TextColumn,
        TransferSpeedColumn,
    )

    with Progress(
        TextColumn("{task.description}"), BarColumn(), DownloadColumn(), TransferSpeedColumn()
    ) as barra:
        tareas: dict[str, TaskID] = {}

        def progreso(nombre: str, recibidos: int, total: int | None) -> None:
            if nombre not in tareas:
                tareas[nombre] = barra.add_task(nombre, total=total)
            barra.update(tareas[nombre], completed=recibidos, total=total)

        try:
            descargar_modelo_e5(dest, progreso=progreso)
        except httpx.HTTPError as exc:
            typer.secho(f"\nNo se pudo descargar el modelo: {exc}", fg="red", err=True)
            typer.echo("Volvé a correr `inaki init` con red; los ficheros ya bajados se conservan.")
            raise typer.Exit(code=1) from exc
    typer.echo(f"  modelo listo en {dest}")
