"""Résolution de chemins fichiers pour les tools (équivalent pratique de shell_paths)."""

from __future__ import annotations

from pathlib import Path


def resolve_path(file_path: str) -> Path:
    """
    Étend ~, résout les chemins relatifs par rapport au répertoire de travail courant.

    Args:
        file_path: Chemin absolu, relatif au CWD, ou avec préfixe ~.

    Returns:
        Chemin Path résolu (symlinks normalisés).
    """
    expanded = Path(file_path).expanduser()
    if expanded.is_absolute():
        return expanded.resolve()
    return (Path.cwd() / expanded).resolve()
