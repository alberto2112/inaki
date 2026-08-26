# AGENTS.md — Inaki

Multi-agent AI assistant with hexagonal architecture, RAG memory, scheduling, and agent delegation.

> `CLAUDE.md` is the canonical guidance file and carries the full rule set. This file is
> the condensed version for other agents. When they disagree, `CLAUDE.md` wins.

## Developer Commands

```
pip install -e ".[dev]"          # install + dev deps
ruff check .                     # lint
ruff format .                    # format (line-length 100)
mypy .                           # type check
pytest                           # all tests
pytest -k test_name              # single test
pytest tests/unit/               # unit only
pytest tests/integration/        # integration only
```

No Makefile, Taskfile, or CI. All commands are direct.

## Architecture (Hexagonal)

```
inaki/                           ← Composition root — entry points
  cli.py                         ← Typer app; the `inaki` command (project.scripts)
  daemon_runner.py               ← systemd service mode
  scheduler_cli.py, knowledge_cli.py, setup_cli.py
core/                            ← Domain layer
  domain/                        ← errors.py, skip_marker.py
    entities/  value_objects/    ← Message, Task, ChannelContext, attachment grammar…
    services/                    ← Domain services (scheduler_service)
    utils/                       ← cron.py (next_cron_occurrence), time_parser.py
  ports/                         ← Interfaces (inbound/outbound contracts)
  use_cases/                     ← Application logic (RunAgent, ConsolidateMemory, …)
adapters/                        ← Implementations of ports
  inbound/                       ← cli, rest, setup_tui, telegram
  outbound/                      ← LLM providers, tools, skills, repos, embedding, faces
infrastructure/                  ← Wiring & cross-cutting
  container.py                   ← DI (AgentContainer, AppContainer) — ONLY place adapters are wired
  config_schema.py               ← Pydantic config models
  config_loader.py               ← 4-layer YAML merge + hot migrations
  config.py                      ← Re-export facade (no logic — schema and loader live above)
  factories/                     ← LLM, embedding and transcription provider factories
ext/                             ← User extensions (auto-discovered via manifest.py)
main.py                          ← Compat wrapper for `python main.py`; real code is inaki/cli.py
```

**Dependency direction**: `adapters → core ← infrastructure`, with `inaki/` above all. Never reversed.

- `core/` NEVER imports `adapters/` or `infrastructure/`. Third-party allowlist: `pydantic`, `croniter`, `numpy` only.
- `adapters/` NEVER imports `infrastructure/`. If an adapter needs the container or the schema, declare a Protocol / Settings VO and let the composition root inject it.
- New entry points go under `inaki/`, NOT under `adapters/inbound/`.

Enforced by `tests/unit/test_architecture.py`. Two rules are **ratchet** (third-party allowlist in `core/`, and `adapters/` not importing `infrastructure/`): their `DEUDA_*` lists are empty and must stay empty.

**Three structural rules** — read `docs/arquitectura.md` before adding a channel or a stateful resource:

1. **Thin channel** — a capability is implemented ONCE (use case in `core/`) and exposed via an LLM tool plus the single admin gateway (`POST /admin/tool/invoke`). A channel only translates its native I/O into a turn. Never re-implement a capability per channel.
2. **Resource tiers** — harness-global singletons (`knowledge`, `scheduler`, `faces`) go in `GlobalConfig` + `AppContainer`; per-agent resources (`memory`, `history`, `channels`, `llm`, `embedding`) go in `AgentConfig` + `AgentContainer`. Never a third ad-hoc pattern.
3. **Wiring** — use cases receive Settings VOs, not `AgentConfig`. Outbound adapters own their `Resolved*Config` DTOs.

## Configuration System

Config lives in `~/.inaki/` by default (NOT in the repo tree). First run bootstraps it.

**4-layer merge** (each layer overrides only fields it defines):
1. `~/.inaki/config/global.yaml`
2. `~/.inaki/config/global.secrets.yaml`
3. `~/.inaki/agents/{id}.yaml`
4. `~/.inaki/agents/{id}.secrets.yaml`

Relocate the whole instance with `--home DIR` (or `INAKI_HOME` env): re-anchors config,
data, `secret.key`, `tool_config`, `users` and knowledge under `DIR/`. Default `~/.inaki`.
See `docs/instance-home.md`.

`config/tool_config.yaml` is daemon-owned and does NOT take part in the merge.

**Critical**: `*.secrets.yaml` files are in `.gitignore`. Never commit them. Agent registry skips files with `.example` in the name.

## LLM, Embedding & Transcription Providers

All three are auto-discovered by scanning for a module-level `PROVIDER_NAME` constant.
The three registries are **independent** — being available as an LLM does not make a
vendor available for transcription.

- **LLM** (`adapters/outbound/providers/`): `anthropic`, `custom`, `deepseek`, `groq`, `ollama`, `openai`, `openai_responses`, `openrouter`. The OpenAI-dialect ones share `OpenAICompatibleProvider`. `custom` is the generic adapter for your own OpenAI-compatible endpoint (vLLM, llama.cpp, an unsloth GGUF on the LAN): classic `max_tokens`, optional `api_key`, no `Authorization` header when there is no key, and `base_url` is required.
- **Embedding** (`adapters/outbound/embedding/`): `e5_onnx` (local ONNX, ARM64-friendly) and `openai`. Dimension is 384 — changing it requires dropping and recreating the memory DB.
- **Transcription** (`adapters/outbound/transcription/`): `groq`, `openai`.

Add a provider by dropping a module in the right folder with `PROVIDER_NAME = "{name}"`.

## Memory & History

SQLite databases, all under `~/.inaki/data/` (paths are `RuntimePath`, anchored to the instance home):

- `inaki.db` — long-term memory with `sqlite-vec` embeddings
- `history.db` — chat history (sliding window) + `agent_state` + face metadata side-table
- `scheduler.db` — scheduled tasks and run logs
- `faces.db` — face embeddings FLOAT[512] (created on first use)
- `embedding_cache.db` — embedding cache

Consolidation extracts memories from chat history via LLM, embeds them, stores them in the
vector DB, then writes a markdown digest to `mem/digest_{channel}_{chat_id}.md` — scoped
per `(channel, chat_id)`, not a single global file.

## Agent Delegation

Two-phase init in `AppContainer`:
1. Build all `AgentContainer` instances
2. Wire delegation (register the `delegate` tool) after all containers exist

Each delegation builds an **ephemeral one-shot child resolved against the CALLER**
(`build_ephemeral_child`): the child inherits the caller's `llm` by default and operates
with the caller's tools/resources, narrowing the visible subset via `tools.allowed`.

`delegate` is async by default (`wait=false`) — it queues and returns `bg-N` immediately.

Recursion prevention is structural: the `delegate` tool is filtered from sub-agent schemas. `max_depth` does not exist as a config field.

## Extensions

User extensions live in `ext/` or `~/.inaki/ext/` (both are searched; see `app.ext_dirs`).
Each extension needs a `manifest.py` exposing `TOOLS` (list of classes) and `SKILLS` (list
of relative file paths). The parent directory is added to `sys.path` so extension-internal
imports resolve.

## Testing

- `pytest-asyncio` mode is `"auto"` — no `@pytest.mark.asyncio` needed
- Fixtures in `tests/conftest.py`: `agent_config`, `mock_llm`, `mock_memory`, `mock_embedder`, `mock_skills`, `mock_history`, `mock_tools`
- `agent_config` fixture uses `:memory:` for DB path — safe for unit tests
- Integration tests require real SQLite files (check `tests/integration/`)

## Code Conventions

Variables, docstrings, comments and error messages are **in Spanish**. Tool `description`
fields are in English (LLM comprehension); `routing_keywords` are multilingual es/en/fr.
Tool results are always `ToolResult` objects. Message roles use the `Role` enum.

## Change Workflow (SDD)

Substantial changes go through the `sdd-*` skills: explore → proposal → spec → design →
tasks → apply → verify → archive.

Artifacts are stored in **engram** by default (no files created). The file-based
`openspec/` store is opt-in and **not currently present in this repo** — do not assume
`openspec/changes/` exists.

## Production Target

Raspberry Pi 5 (4GB RAM) via systemd (`systemd/inaki.service`), `MemoryMax=2G`, user `pi`.
Code in `/home/pi/inaki/`; config and data under `/home/pi/.inaki/` (the instance home).

---

## References

Full index in `CLAUDE.md` → "Referencias". The essentials:

- **Architecture rules**: `docs/arquitectura.md` — layers, thin channel, resource tiers, wiring
- **Per-subsystem conventions**: `docs/convenciones.md`
- **Migration history / breaking changes**: `docs/migraciones.md`
- **Tech Spec**: `docs/inaki_spec.md`
- **Data Model**: `docs/modelo_de_datos.md`
- **Execution Flow**: `docs/flujo_ejecucion.md`
- **Config Reference**: `docs/configuracion.md` (índice) and `docs/config-reference.md` (autogenerated)
- **Scheduler Spec**: `docs/scheduler-spec.md`
- **GitHub**: https://github.com/alberto2112/inaki
