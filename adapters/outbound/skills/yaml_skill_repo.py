"""
YamlSkillRepository — carga skills desde YAML y las recupera via cosine similarity.

Estructura esperada de cada skill YAML:
  id: "web_search"
  name: "Búsqueda Web"
  description: "Busca información en internet usando DuckDuckGo"
  instructions: |
    Cuando el usuario pregunta sobre eventos actuales o necesita información...
  tags:
    - "búsqueda"
    - "internet"
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import yaml

from core.domain.entities.skill import Skill
from core.ports.outbound.embedding_port import IEmbeddingProvider
from core.ports.outbound.skill_port import ISkillRepository

logger = logging.getLogger(__name__)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    va = np.array(a, dtype=np.float32)
    vb = np.array(b, dtype=np.float32)
    norm_a = np.linalg.norm(va)
    norm_b = np.linalg.norm(vb)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(va, vb) / (norm_a * norm_b))


class YamlSkillRepository(ISkillRepository):

    def __init__(self, skills_dir: str, embedder: IEmbeddingProvider) -> None:
        self._skills_dir = Path(skills_dir)
        self._embedder = embedder
        self._extra_files: list[Path] = []
        self._skills: list[Skill] = []
        self._embeddings: list[list[float]] = []
        self._loaded = False

    def add_file(self, path: Path) -> None:
        """Registra un YAML de skill adicional fuera de skills_dir. Invalida cache."""
        path = Path(path).resolve()
        if path in [p.resolve() for p in self._extra_files]:
            return
        self._extra_files.append(path)
        self._loaded = False

    async def _load_skill_from_path(self, yaml_file: Path) -> None:
        try:
            with yaml_file.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            skill = Skill(
                id=data.get("id", yaml_file.stem),
                name=data.get("name", yaml_file.stem),
                description=data.get("description", ""),
                instructions=data.get("instructions", ""),
                tags=data.get("tags", []),
            )
            text = f"{skill.name} {skill.description} {' '.join(skill.tags)}"
            embedding = await self._embedder.embed_passage(text)
            self._skills.append(skill)
            self._embeddings.append(embedding)
            logger.debug("Skill cargada: '%s' (%s)", skill.id, yaml_file)
        except Exception as exc:
            logger.warning("Error cargando skill %s: %s", yaml_file, exc)

    async def _ensure_loaded(self) -> None:
        if self._loaded:
            return

        self._skills = []
        self._embeddings = []
        seen: set[Path] = set()

        # 1. Directorio base (built-ins)
        if self._skills_dir.exists():
            for yaml_file in sorted(self._skills_dir.rglob("*.yaml")):
                resolved = yaml_file.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    await self._load_skill_from_path(yaml_file)
        else:
            logger.warning("Directorio de skills no encontrado: %s", self._skills_dir)

        # 2. Archivos extra (desde extensiones)
        for extra in self._extra_files:
            resolved = extra.resolve()
            if resolved in seen:
                logger.debug("Skill extra ya cargada: %s", extra)
                continue
            seen.add(resolved)
            await self._load_skill_from_path(extra)

        logger.info("YamlSkillRepository: %d skill(s) cargada(s)", len(self._skills))
        self._loaded = True

    async def list_all(self) -> list[Skill]:
        await self._ensure_loaded()
        return list(self._skills)

    async def retrieve(
        self,
        query_embedding: list[float],
        top_k: int = 3,
    ) -> list[Skill]:
        await self._ensure_loaded()
        if not self._skills:
            return []

        scored = [
            (skill, _cosine_similarity(query_embedding, emb))
            for skill, emb in zip(self._skills, self._embeddings)
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [skill for skill, _ in scored[:top_k]]
