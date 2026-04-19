"""
knowledge_cli — comandos de gestión de fuentes de conocimiento.

Sub-app de Typer con comandos:
  knowledge index <source-id>   — indexa o re-indexa una fuente
  knowledge list                — lista fuentes configuradas
  knowledge stats <source-id>   — muestra estadísticas del índice de una fuente
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

knowledge_app = typer.Typer(help="Manage document knowledge sources.")


def _load_global_config(config_dir: Path | None = None):
    """Carga la configuración global. Retorna GlobalConfig."""
    from infrastructure.config import ensure_user_config, load_global_config

    if config_dir is None:
        config_dir = Path.home() / ".inaki" / "config"
        agents_dir = Path.home() / ".inaki" / "agents"
        ensure_user_config(config_dir, agents_dir)
    else:
        agents_dir = config_dir / "agents"

    try:
        global_cfg, _ = load_global_config(config_dir)
        return global_cfg
    except Exception as exc:
        typer.echo(f"Error loading config from {config_dir}: {exc}", err=True)
        raise typer.Exit(code=1)


def _get_source_config(global_cfg, source_id: str):
    """Busca una fuente de conocimiento por ID. Sale con código 1 si no existe."""
    fuentes = global_cfg.knowledge.sources
    for fuente in fuentes:
        if fuente.id == source_id:
            return fuente
    typer.echo(
        f"Error: unknown source '{source_id}'. "
        f"Configured sources: {[s.id for s in fuentes] or '(none)'}",
        err=True,
    )
    raise typer.Exit(code=1)


def _build_document_source(fuente_cfg, global_cfg):
    """Instancia un DocumentKnowledgeSource a partir de la config de fuente."""
    from infrastructure.factories.embedding_factory import EmbeddingProviderFactory

    if fuente_cfg.type != "document":
        typer.echo(
            f"Error: source '{fuente_cfg.id}' has type '{fuente_cfg.type}', "
            "only 'document' sources can be indexed via CLI.",
            err=True,
        )
        raise typer.Exit(code=1)

    if fuente_cfg.path is None:
        typer.echo(
            f"Error: source '{fuente_cfg.id}' has no 'path' configured.",
            err=True,
        )
        raise typer.Exit(code=1)

    from adapters.outbound.knowledge.document_knowledge_source import DocumentKnowledgeSource

    # Crear embedder mínimo (no necesita config de agente completa, usamos global)
    # Construimos un AgentConfig mínimo solo para el factory
    import types

    fake_cfg = types.SimpleNamespace(
        embedding=global_cfg.embedding,
        llm=global_cfg.llm,
    )

    try:
        embedder = EmbeddingProviderFactory.create(fake_cfg)
    except Exception as exc:
        typer.echo(f"Error initializing embedder: {exc}", err=True)
        raise typer.Exit(code=1)

    return DocumentKnowledgeSource(
        source_id=fuente_cfg.id,
        description=fuente_cfg.description,
        path=fuente_cfg.path,
        embedder=embedder,
        glob=fuente_cfg.glob,
        chunk_size=fuente_cfg.chunk_size,
        chunk_overlap=fuente_cfg.chunk_overlap,
        dimension=global_cfg.embedding.dimension,
    )


@knowledge_app.command("index")
def knowledge_index(
    ctx: typer.Context,
    source_id: str = typer.Argument(..., help="ID of the knowledge source to index."),
) -> None:
    """Index (or re-index) a document knowledge source."""
    config_dir: Path | None = ctx.obj.get("config_dir") if ctx.obj else None

    global_cfg = _load_global_config(config_dir)
    fuente_cfg = _get_source_config(global_cfg, source_id)
    source = _build_document_source(fuente_cfg, global_cfg)

    typer.echo(f"Indexing source '{source_id}' from {fuente_cfg.path} ...")

    async def _run():
        return await source.index()

    stats = asyncio.run(_run())

    typer.echo(
        f"Done. Files processed: {stats['archivos_procesados']}, "
        f"skipped (unchanged): {stats['archivos_saltados']}, "
        f"new chunks: {stats['chunks_nuevos']}"
    )


@knowledge_app.command("list")
def knowledge_list(
    ctx: typer.Context,
) -> None:
    """List all configured knowledge sources."""
    config_dir: Path | None = ctx.obj.get("config_dir") if ctx.obj else None

    global_cfg = _load_global_config(config_dir)
    fuentes = global_cfg.knowledge.sources

    if not fuentes:
        typer.echo("No knowledge sources configured.")
        return

    typer.echo(f"{'ID':<20} {'TYPE':<12} {'ENABLED':<8} {'PATH / DESCRIPTION'}")
    typer.echo("-" * 70)
    for f in fuentes:
        ubicacion = f.path or f.description or "-"
        estado = "yes" if f.enabled else "no"
        typer.echo(f"{f.id:<20} {f.type:<12} {estado:<8} {ubicacion}")


@knowledge_app.command("stats")
def knowledge_stats(
    ctx: typer.Context,
    source_id: str = typer.Argument(..., help="ID of the knowledge source."),
) -> None:
    """Show index statistics for a knowledge source."""
    config_dir: Path | None = ctx.obj.get("config_dir") if ctx.obj else None

    global_cfg = _load_global_config(config_dir)
    fuente_cfg = _get_source_config(global_cfg, source_id)
    source = _build_document_source(fuente_cfg, global_cfg)

    async def _run():
        return await source.get_stats()

    stats = asyncio.run(_run())

    typer.echo(f"Source:          {stats['source_id']}")
    typer.echo(f"DB path:         {stats['db_path']}")
    typer.echo(f"Files indexed:   {stats['archivos_indexados']}")
    typer.echo(f"Total chunks:    {stats['chunks_totales']}")
    typer.echo(f"Embedding dim:   {stats['embedding_dimension']}")

    last_mtime = stats.get("last_indexed_mtime")
    if last_mtime is None:
        typer.echo("Last indexed:    (never)")
    else:
        from datetime import datetime, timezone

        ts = datetime.fromtimestamp(float(last_mtime), tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
        typer.echo(f"Last indexed:    {ts}")
