# Design: memory-digest-markdown

## 1. Technical Approach

Approach A (inline minimal): remove `memory.search` from the run-agent hot path and replace it with a markdown digest that is regenerated at the end of `/consolidate` and read at the top of every turn. No new port is introduced for the digest file itself — `Path.read_text`/`Path.write_text` live directly inside the two use cases. The existing `IMemoryRepository.get_recent(limit: int = 10)` (port line 18, SQLite adapter line 120) is reused as-is to source the digest — no new port method is added. The `embed_query` call is moved INSIDE the `skills_rag_active or tools_rag_active` branch so a fully-inactive RAG turn performs zero embedding calls. `~` in `memory.digest_path` is expanded at config load time via a pydantic `field_validator`, so use cases receive an already-resolved absolute `Path`. `SQLiteMemoryRepository.search` stays intact as dormant code for a future on-demand recall tool.

---

## 2. Architecture Decisions

### 2.1 No new `IMemoryDigestStore` port for the markdown file

**Choice**: Inline `Path.read_text` / `Path.write_text` directly in `RunAgentUseCase` and `ConsolidateMemoryUseCase`.
**Alternatives considered**: Dedicated `IMemoryDigestStore` port + `MarkdownMemoryDigest` adapter + container wiring.
**Rationale**: Two call sites and one tiny responsibility (read-one-file, write-one-file) do not justify a port + adapter + wiring + mocks. If the digest ever needs a different format (JSON, YAML) or a remote backend, extracting a port later is a mechanical ~30-minute refactor. Over-engineering now buys nothing. The codebase already accepts filesystem I/O directly in infrastructure-adjacent use case code (see `ConsolidateMemoryUseCase` writing to history store via a port, but using vanilla `Path.mkdir` locally when needed).

### 2.2 `embed_query` moved INSIDE the RAG-active branch

**Choice**: Compute `all_skills` and `all_schemas`, derive `skills_rag_active` / `tools_rag_active`, and only then call `embed_query` inside `if skills_rag_active or tools_rag_active`.
**Alternatives considered**: Keep the unconditional `embed_query` call and just stop using it when no RAG is active.
**Rationale**: The entire point of the change is "zero embedding calls per turn when neither RAG is active". Keeping the call would defeat the performance goal. The branch is trivial and the flag names are already used downstream, so readability does not suffer.

### 2.3 Digest regeneration placed BEFORE `history.archive`/`clear`

**Choice**: In `ConsolidateMemoryUseCase.execute()`, call `_write_digest()` AFTER the `memory.store` loop, BEFORE `history.archive` and `history.clear` (current lines 151-156).
**Alternatives considered**: Write the digest AFTER archive+clear (strict "history-first, artifacts-later" order).
**Rationale**: Failure-mode analysis:
- Digest first, archive fails: digest is correct and up-to-date for the already-stored memories. User re-runs `/consolidate`; stored memories are idempotent-ish (embeddings re-computed, but `INSERT OR REPLACE` by `id` keeps it safe), archive retries and succeeds. Digest is never stale.
- Archive first, digest fails: history is already gone, but digest still reflects the PREVIOUS consolidate. Any new memories stored in this run are NOT in the digest until the NEXT consolidate runs. This is a silent staleness window.
Putting the digest first means the digest is always at least as fresh as the latest successfully-stored memory. This is the fail-safer order.

### 2.4 `~` expansion happens at config load time

**Choice**: Add a pydantic `field_validator` on `MemoryConfig.digest_path` that returns `Path(value).expanduser()`. Use cases receive an already-resolved absolute `Path`.
**Alternatives considered**: Store the raw string and expand lazily inside the use cases (`Path(cfg.memory.digest_path).expanduser()` at the call site).
**Rationale**: Fail-fast is better than fail-lazy. If expansion ever fails (extremely unlikely but possible with malformed paths), the config load raises immediately with a clear error, not deep inside a turn. It also keeps the use cases pure: they do not need to know the path might contain `~`. Every caller benefits from the single resolution point.

### 2.5 `memory_digest` as a plain `str` in `AgentContext`

**Choice**: `memory_digest: str = ""`. Concatenated verbatim into the system prompt with a leading blank line. The markdown file itself already contains the `# Recuerdos sobre el usuario` header, so `AgentContext` does NOT wrap it in another heading.
**Alternatives considered**: A `MemoryDigest` value object with `generated_at`, `entries: list[...]`, etc.
**Rationale**: The digest is already human-readable markdown. Parsing it into a structured object just to re-serialize it into the prompt is pointless churn. Keeping it as a string means zero coupling between the consolidate renderer and the prompt builder — the format can evolve freely.

### 2.6 `search` stays alive in the adapter

**Choice**: `SQLiteMemoryRepository.search` and `IMemoryRepository.search` remain defined and tested. Only the CALL site in `run_agent.py` is removed.
**Alternatives considered**: Delete `search` entirely since it has no live callers.
**Rationale**: Out-of-scope-but-imminent work: a future LLM tool that lets the agent search old memories on-demand. Deleting `search` now would force re-implementing it weeks later. Keeping it costs nothing (no code runs) and preserves the vector-search capability.

---

## 3. Data Flow

### Turn flow (after)

```
RunAgentUseCase.execute(user_input)
  |-> history.load(agent_id)
  |-> digest_text = _read_digest()             <- NEW
  |-> all_skills    = skills.list_all()
  |-> all_schemas   = tools.get_schemas()
  |-> skills_rag_active = len(all_skills)  > cfg.skills.rag_min_skills
  |-> tools_rag_active  = len(all_schemas) > cfg.tools.rag_min_tools
  |-> IF skills_rag_active or tools_rag_active:
  |      query_vec = embed_query(user_input)   <- NOW conditional
  |      IF skills_rag_active: retrieved_skills = skills.retrieve(query_vec, ...)
  |      IF tools_rag_active:  tool_schemas    = tools.get_schemas_relevant(query_vec, ...)
  |   ELSE:
  |      retrieved_skills = all_skills
  |      tool_schemas     = all_schemas
  |-> context = AgentContext(agent_id, memory_digest=digest_text, skills=retrieved_skills)
  |-> system_prompt = context.build_system_prompt(cfg.system_prompt)
  |-> response = _run_with_tools(messages, system_prompt, tool_schemas)
  |-> history.append(user) + history.append(assistant)
```

No `memory.search` in the hot path. No `embed_query` at all when both flags are false.

### Consolidate flow (after)

```
ConsolidateMemoryUseCase.execute()
  |-> messages = history.load_full(agent_id)
  |-> (if empty -> early return)
  |-> LLM extract facts -> list[dict]
  |-> for each fact: embed_passage + memory.store
  |-> _write_digest():                                    <- NEW
  |     latest   = memory.get_recent(cfg.memory.digest_size)
  |     markdown = _render_digest(latest)
  |     cfg.memory.digest_path.parent.mkdir(parents=True, exist_ok=True)
  |     cfg.memory.digest_path.write_text(markdown, encoding="utf-8")
  |-> history.archive(agent_id)     (unchanged, current line 153)
  |-> history.clear(agent_id)       (unchanged, current line 154)
```

---

## 4. Module Design

### 4.1 `core/use_cases/run_agent.py`

**New private method**:

```python
def _read_digest(self) -> str:
    """Lee el digest markdown. Retorna '' si no existe o falla la lectura."""
    path = self._cfg.memory.digest_path  # already an expanded Path (validator)
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.debug("Digest no encontrado en %s — primera vez o sin consolidate", path)
        return ""
    except OSError as exc:
        logger.warning("No se pudo leer el digest %s: %s", path, exc)
        return ""
```

**Modified `execute()`** — new block replacing current lines 78-93:

```python
# 1. Cargar historial
history = await self._history.load(agent_id)

# 2. Leer digest de memoria (reemplaza memory.search)
digest_text = self._read_digest()

# 3. Precomputar skills/tools y decidir si RAG está activo
all_skills  = await self._skills.list_all()
all_schemas = self._tools.get_schemas()
skills_rag_active = len(all_skills)  > self._cfg.skills.rag_min_skills
tools_rag_active  = len(all_schemas) > self._cfg.tools.rag_min_tools

# 4. Embedding SOLO si algún RAG lo necesita
retrieved_skills: list[Skill] = all_skills
tool_schemas: list[dict]      = all_schemas
if skills_rag_active or tools_rag_active:
    query_vec = await self._embedder.embed_query(user_input)
    if skills_rag_active:
        retrieved_skills = await self._skills.retrieve(
            query_vec, top_k=self._cfg.skills.rag_top_k
        )
    if tools_rag_active:
        tool_schemas = await self._tools.get_schemas_relevant(
            query_vec, top_k=self._cfg.tools.rag_top_k
        )

# 5. Construir context y system prompt
context = AgentContext(
    agent_id=agent_id,
    memory_digest=digest_text,
    skills=retrieved_skills,
)
system_prompt = context.build_system_prompt(self._cfg.system_prompt)
```

`top_k` on line 73 and `memories = await self._memory.search(...)` on line 80 are DELETED.

**Modified `inspect()`**: same reorder. Also calls `_read_digest` and returns `memory_digest` in `InspectResult`.

**Modified `InspectResult`** (lines 38-48):

```python
@dataclass
class InspectResult:
    user_input: str
    memory_digest: str              # was: memories: list[MemoryEntry]
    all_skills: list[Skill]
    selected_skills: list[Skill]
    skills_rag_active: bool
    all_tool_schemas: list[dict]
    selected_tool_schemas: list[dict]
    tools_rag_active: bool
    system_prompt: str
```

Remove the unused `from core.domain.entities.memory import MemoryEntry` import if no longer referenced.

### 4.2 `core/use_cases/consolidate_memory.py`

**New private methods**:

```python
def _render_digest(self, memories: list[MemoryEntry]) -> str:
    """Renderiza la lista de memorias como markdown human-readable."""
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    lines = [
        "# Recuerdos sobre el usuario",
        f"<!-- Generado por /consolidate — {now_iso} -->",
        "",
    ]
    for m in memories:
        # created_at legacy fallback: usar "ahora" si es None para no romper render
        date_str = (m.created_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
        tag_suffix = f" ({', '.join(m.tags)})" if m.tags else ""
        lines.append(f"- [{date_str}] {m.content}{tag_suffix}")
    return "\n".join(lines) + "\n"

async def _write_digest(self) -> None:
    """Regenera el digest markdown. Nunca propaga excepciones."""
    try:
        latest = await self._memory.get_recent(self._memory_cfg.digest_size)
        markdown = self._render_digest(latest)
        path = self._memory_cfg.digest_path  # ya expandido por el validator
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
        logger.info("Digest regenerado: %s (%d recuerdos)", path, len(latest))
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.error("No se pudo regenerar el digest: %s", exc)
```

`_write_digest` **never raises**. Failing to write the digest MUST NOT fail the consolidation — the user's main expectation is "my conversation got archived and my facts got stored". A failed digest is recoverable on the next consolidate.

**Integration in `execute()`**: after the `for fact in facts` loop (current line 149), BEFORE line 152 `archive_path = await self._history.archive(...)`:

```python
# 5b. Regenerar digest markdown (best-effort, no rompe consolidación)
await self._write_digest()

# 6. Archivar historial (unchanged)
try:
    archive_path = await self._history.archive(self._agent_id)
    await self._history.clear(self._agent_id)
...
```

**Constructor change**: the use case currently does NOT receive a `MemoryConfig`. Add one:

```python
def __init__(
    self,
    llm: ILLMProvider,
    memory: IMemoryRepository,
    embedder: IEmbeddingProvider,
    history: IHistoryStore,
    agent_id: str,
    memory_config: MemoryConfig,        # NEW
) -> None:
    ...
    self._memory_cfg = memory_config
```

Container wiring in `infrastructure/container.py` (or wherever `ConsolidateMemoryUseCase` is built) must pass `agent_cfg.memory`.

### 4.3 `core/domain/value_objects/agent_context.py`

```python
from pydantic import BaseModel
from core.domain.entities.skill import Skill


class AgentContext(BaseModel):
    agent_id: str
    memory_digest: str = ""            # was: memories: list[MemoryEntry] = []
    skills: list[Skill] = []

    def build_system_prompt(self, base_prompt: str) -> str:
        sections = [base_prompt]

        if self.memory_digest.strip():
            # El digest ya trae su propio header "# Recuerdos sobre el usuario".
            # Lo concatenamos tal cual, con una línea en blanco de separación.
            sections.append("\n" + self.memory_digest)

        if self.skills:
            skill_block = "\n".join(
                f"- **{s.name}**: {s.description}"
                + (f"\n  {s.instructions}" if s.instructions else "")
                for s in self.skills
            )
            sections.append(f"\n## Skills disponibles:\n{skill_block}")

        return "\n".join(sections)
```

The `from core.domain.entities.memory import MemoryEntry` import is removed — no longer used.

### 4.4 `core/ports/outbound/memory_port.py`

**Unchanged.** The existing `get_recent(limit: int = 10) -> list[MemoryEntry]` abstract method (current line 18) is reused as-is. It already declares the exact signature and contract the digest needs — return memories ordered by `created_at DESC` and limited to `limit`. No new port method is introduced.

### 4.5 `adapters/outbound/memory/sqlite_memory_repo.py`

**Unchanged.** The existing `get_recent` implementation (current line 120) already runs:

```sql
SELECT id, content, relevance, tags, created_at, agent_id
FROM memories
ORDER BY created_at DESC
LIMIT ?
```

…which is exactly the query the digest needs. It does not join `memory_embeddings`, which is optimal for rendering (embeddings are not used by `_render_digest`). `_row_to_entry` (line 130) already returns `embedding=[]`, so no adapter changes are required.

`search` is also **unchanged** and stays available for the future on-demand memory tool (see decision 2.6). The decision to reuse `get_recent` instead of adding a dedicated `list_latest` method avoids pure duplication — both methods would wrap the identical SQL. If `get_recent` ever diverges semantically from "digest-source" needs (e.g. default limit changes, filtering added), a dedicated method can be introduced at that time with zero interface fallout.

### 4.6 `infrastructure/config.py`

```python
from pathlib import Path
from pydantic import BaseModel, field_validator


class MemoryConfig(BaseModel):
    db_path: str = "data/inaki.db"
    default_top_k: int = 5
    digest_size: int = 14
    digest_path: Path = Path("~/.inaki/mem/last_memories.md")

    @field_validator("digest_path", mode="before")
    @classmethod
    def _expand_digest_path(cls, v) -> Path:
        # Runs for EXPLICIT values passed to the constructor / loaded from YAML.
        return Path(v).expanduser()

    def model_post_init(self, __context) -> None:
        # Pydantic v2 field_validator does NOT run on class-level defaults —
        # only on explicitly-provided values. model_post_init catches the default
        # case so digest_path is always an absolute Path, regardless of how the
        # instance was constructed.
        if "~" in str(self.digest_path):
            object.__setattr__(self, "digest_path", self.digest_path.expanduser())
```

> **Note (added during archive)**: The original design showed only `field_validator`, but pydantic v2 field validators do NOT run on class-level default values — they only run when a value is explicitly passed to the constructor or parsed from input. `MemoryConfig()` with no arguments would leave `digest_path` un-expanded. `model_post_init` is the idiomatic pydantic v2 hook for post-construction normalization; it runs on every instance and ensures `digest_path` is always an absolute `Path`. This was discovered during apply (batch 1) and verified during `sdd-verify` (INV-7 PASS).

`MemoryConfig` is already a pydantic `BaseModel` (verified — line 64 of the current file), so no restructuring needed. Both the validator and `model_post_init` together guarantee that `AgentConfig.memory.digest_path` is always an absolute `Path` by the time a use case sees it — whether the value came from YAML, an explicit constructor call, or the class-level default.

### 4.7 `config/global.example.yaml`

Under the `memory` section, after `default_top_k: 5`:

```yaml
  digest_size: 14             # Nº de recuerdos más recientes volcados al digest markdown
                              # tras cada /consolidate. Orden: created_at DESC.

  digest_path: "~/.inaki/mem/last_memories.md"
                              # Ruta del fichero markdown leído por el prompt builder
                              # en cada turno. Soporta expansión de ~ (home del usuario).
                              # Parte del principio user-data-separation: los datos del
                              # usuario viven fuera del árbol del proyecto.
                              # Producción Pi 5: "/home/pi/.inaki/mem/last_memories.md"
```

---

## 5. Error Handling Strategy

| Failure mode | Component | Behavior |
|---|---|---|
| Digest file missing (first run, pre-consolidate) | `RunAgentUseCase._read_digest` | Catch `FileNotFoundError`, log DEBUG, return `""`. Turn proceeds with no memory section in the prompt. |
| Digest file unreadable (permissions, corrupted) | `RunAgentUseCase._read_digest` | Catch `OSError`, log WARNING, return `""`. Turn proceeds. |
| `list_latest` DB error | `ConsolidateMemoryUseCase._write_digest` | Caught by blanket `except Exception`, logged as ERROR, digest skipped. `archive` + `clear` still run. |
| `_write_digest` IOError (full disk, permissions, parent dir unwritable) | `ConsolidateMemoryUseCase._write_digest` | Caught by blanket `except Exception`, logged as ERROR, digest skipped. `archive` + `clear` still run. |
| `~` expansion fails on config load | `MemoryConfig._expand_digest_path` | `Path.expanduser()` does not normally raise; if it does, pydantic wraps it into a `ValidationError` at load time — fail fast, the application never starts with a broken path. |
| Legacy `MemoryEntry` rows with `created_at=None` | `ConsolidateMemoryUseCase._render_digest` | Defensive `(m.created_at or datetime.now(timezone.utc))` fallback in the renderer. Verified to be dead code in practice: `SQLiteMemoryRepository._row_to_entry` (line 137) calls `datetime.fromisoformat(row["created_at"])` without a guard, and the only writer (`consolidate_memory.py:142`) always sets `created_at=datetime.now(timezone.utc)`. Kept as belt-and-braces but is expected to never trigger. |
| `memory_digest` is whitespace-only | `AgentContext.build_system_prompt` | `if self.memory_digest.strip():` skips the section entirely — no stray blank lines in the prompt. |

---

## 6. Testing Strategy

| Test | Approach | File |
|---|---|---|
| FR-01: `embed_query` is NOT called when `skills_rag_active == False and tools_rag_active == False` | `AsyncMock()` embedder, single skill, no tools, run `execute`. Assert `embedder.embed_query.call_count == 0`. | `tests/unit/use_cases/test_run_agent_basic.py` |
| FR-02: `embed_query` IS called when `skills_rag_active == True` | Populate `list_all()` with `rag_min_skills + 1` skills. Assert `call_count == 1`. | `tests/unit/use_cases/test_run_agent_basic.py` |
| FR-03: `memory.search` is NEVER called from `execute` | `AsyncMock` memory repo; assert `memory.search.call_count == 0` after `execute`. | `tests/unit/use_cases/test_run_agent_basic.py` |
| FR-04: Digest text is read and injected into the system prompt | Write a tmp digest file, point `cfg.memory.digest_path` at it, run `execute`, capture `llm.complete` system_prompt argument, assert the digest content is in it. | `tests/unit/use_cases/test_run_agent_basic.py` |
| FR-05: Missing digest file degrades gracefully | Point `digest_path` at nonexistent file, run `execute`, assert no exception raised and system prompt contains `base_prompt` but no memory section. | `tests/unit/use_cases/test_run_agent_basic.py` |
| FR-06: `_read_digest` swallows `OSError` | Simulate unreadable file (patch `Path.read_text` to raise `PermissionError`), assert returns `""` and logs WARNING. | `tests/unit/use_cases/test_run_agent_basic.py` |
| FR-07: `ConsolidateMemoryUseCase` writes digest file with agreed format after successful consolidation | Use `tmp_path` fixture, run a full consolidate with fake facts, assert file exists, starts with `# Recuerdos sobre el usuario`, contains the generated timestamp comment, and each memory line matches `- [YYYY-MM-DD] content (tag1, tag2)`. | `tests/unit/use_cases/test_consolidate_memory.py` |
| FR-08: `archive` + `clear` still invoked after digest write | Spy on `history.archive` and `history.clear`, assert both called exactly once after `_write_digest` in that order. | `tests/unit/use_cases/test_consolidate_memory.py` |
| FR-09: Digest write failure does NOT abort consolidation | Patch `Path.write_text` to raise `OSError`, run `execute`, assert `history.archive` and `history.clear` are still called and no exception propagates. | `tests/unit/use_cases/test_consolidate_memory.py` |
| FR-10: `ConsolidateMemoryUseCase` calls `get_recent` with `cfg.memory.digest_size` | `AsyncMock` memory repo; run `execute`; assert `memory.get_recent` was called once with `limit=cfg.memory.digest_size`. Ordering/limit contract of `get_recent` is covered by pre-existing adapter tests — no new adapter test added. | `tests/unit/use_cases/test_consolidate_memory.py` |
| NFR-01: `search` adapter method still works (regression guard) | Keep existing adapter tests green. No new test needed. | `tests/unit/adapters/test_sqlite_memory_repo.py` |
| NFR-02: `~` in `digest_path` is resolved at load time | Construct `MemoryConfig(digest_path="~/test.md")`, assert `cfg.digest_path.is_absolute()` and does not contain `~`. | `tests/unit/infrastructure/test_config.py` |
| NFR-03: `AgentContext` renders empty digest cleanly | `AgentContext(memory_digest="")` → `build_system_prompt(base)` returns exactly `base` (no stray blank lines or headers). | `tests/unit/domain/test_agent_context.py` |
| NFR-04: `AgentContext` renders a non-empty digest verbatim | `AgentContext(memory_digest="# foo\n- bar")` → system prompt contains `# foo\n- bar` without additional wrapping. | `tests/unit/domain/test_agent_context.py` |
| NFR-05: `InspectResult.memory_digest` matches what `execute` would inject | Run `inspect` with the same digest fixture, assert `result.memory_digest == file contents`. | `tests/unit/use_cases/test_run_agent_basic.py` |

All use-case tests use `AsyncMock` for outbound ports. Adapter tests use a real `tmp_path` SQLite DB with the `sqlite-vec` extension loaded — same pattern as existing adapter tests.

---

## 7. Resolved Questions

All four questions flagged by the initial design draft were resolved before the tasks phase by reading the real code. None remain open.

- **Q1 (Legacy `created_at` values) — RESOLVED, not a real concern.** `SQLiteMemoryRepository._row_to_entry` (line 137) already calls `datetime.fromisoformat(row["created_at"])` without a guard. If any row had `NULL` or empty `created_at`, the adapter would already crash today on `get_recent` / `search`. The only writer, `ConsolidateMemoryUseCase.execute` line 142, always sets `created_at=datetime.now(timezone.utc)`. The defensive `(m.created_at or datetime.now(timezone.utc))` fallback in `_render_digest` is kept as belt-and-braces but is expected to never trigger.

- **Q2 (CLI debug consumer of `InspectResult`) — RESOLVED, single location.** Only `adapters/inbound/cli/cli_runner.py` lines 111-113 reads `result.memories`. It currently prints `f"📍 Memorias recuperadas ({len(result.memories)}):"` followed by a `for m in result.memories` loop. This must be updated during apply to print the `memory_digest` string directly (e.g. replace the loop with a single `print(result.memory_digest or "(sin digest)")`).

- **Q3 (Container wiring for `memory_config`) — RESOLVED, mechanical change.** `infrastructure/container.py:66-72` currently builds the use case as:

  ```python
  self.consolidate_memory = ConsolidateMemoryUseCase(
      llm=self._llm,
      memory=self._memory,
      embedder=self._embedder,
      history=self._history,
      agent_id=cfg.id,
  )
  ```

  Apply must add `memory_config=cfg.memory,` as a final kwarg. No other wiring changes.

- **Q4 (`get_recent` vs `list_latest` redundancy) — RESOLVED, reuse `get_recent`.** The existing `IMemoryRepository.get_recent(limit: int = 10) -> list[MemoryEntry]` (port line 18, adapter line 120) already implements the exact SQL (`ORDER BY created_at DESC LIMIT ?`) that the digest needs. Adding a separate `list_latest` method would be pure duplication. Sections 4.4 and 4.5 were revised to reflect no new port method; the port and adapter are unchanged. `_write_digest` calls `get_recent(self._memory_cfg.digest_size)`.
