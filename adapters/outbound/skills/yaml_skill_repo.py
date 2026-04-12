"""
YamlSkillRepository — carga skills desde YAML y las recupera via cosine similarity.

Las skills se registran exclusivamente vía `add_file()`, invocado por los
manifest.py de las extensiones del usuario. No hay skills built-in ni directorio
base — el core no define saber de dominio.

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

import hashlib
import logging
from pathlib import Path

import yaml

from adapters.outbound.embedding import resolve_provider_name
from core.domain.entities.skill import Skill
from core.domain.services.similarity import cosine_similarity
from core.ports.outbound.embedding_cache_port import IEmbeddingCache
from core.ports.outbound.embedding_port import IEmbeddingProvider
from core.ports.outbound.skill_port import ISkillRepository

logger = logging.getLogger(__name__)


class YamlSkillRepository(ISkillRepository):

    def __init__(
        self,
        embedder: IEmbeddingProvider,
        cache: IEmbeddingCache | None = None,
        dimension: int = 384,
    ) -> None:
        self._embedder = embedder
        self._cache = cache
        self._dimension = dimension
        self._provider_name = resolve_provider_name(embedder)
        self._extra_files: list[Path] = []
        self._skills: list[Skill] = []
        self._embeddings: list[list[float]] = []
        self._loaded = False

    def add_file(self, path: Path) -> None:
        """Registra un YAML de skill. Invalida cache."""
        path = Path(path).resolve()
        if path in [p.resolve() for p in self._extra_files]:
            return
        self._extra_files.append(path)
        self._loaded = False

    async def _load_skill_from_path(self, yaml_file: Path) -> None:
        try:
            raw_bytes = yaml_file.read_bytes()
            content_hash = hashlib.md5(raw_bytes).hexdigest()

            # Intentar obtener embedding del cache
            embedding: list[float] | None = None
            if self._cache is not None:
                embedding = await self._cache.get(
                    content_hash, self._provider_name, self._dimension
                )

            data = yaml.safe_load(raw_bytes.decode("utf-8")) or {}
            skill = Skill(
                id=data.get("id", yaml_file.stem),
                name=data.get("name", yaml_file.stem),
                description=data.get("description", ""),
                instructions=data.get("instructions", ""),
                tags=data.get("tags", []),
            )

            if embedding is None:
                text = f"{skill.name} {skill.description} {' '.join(skill.tags)}"
                embedding = await self._embedder.embed_passage(text)
                if self._cache is not None:
                    await self._cache.put(
                        content_hash, self._provider_name, self._dimension, embedding
                    )

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

        for extra in self._extra_files:
            resolved = extra.resolve()
            if resolved in seen:
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
        min_score: float = 0.0,
    ) -> list[Skill]:
        await self._ensure_loaded()
        if not self._skills:
            return []

        scored = [
            (skill, cosine_similarity(query_embedding, emb))
            for skill, emb in zip(self._skills, self._embeddings)
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        if min_score > 0.0:
            scored = [(skill, s) for skill, s in scored if s >= min_score]
        return [skill for skill, _ in scored[:top_k]]
