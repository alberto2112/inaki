# AGENTS.md — Iñaki v2

Multi-agent AI assistant with hexagonal architecture, RAG memory, scheduling, and agent delegation.

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
main.py                          ← Typer CLI entry point (`inaki` command)
core/                            ← Domain layer (entities, errors, use cases, services)
  domain/                        ← Entities (Message, Task), domain errors
  ports/                         ← Interfaces (provider contracts)
  use_cases/                     ← Application logic (RunAgent, ConsolidateMemory, etc.)
  services/                      ← Domain services (scheduler_service)
adapters/                        ← Implementation of ports
  inbound/                       ← CLI, daemon, Telegram bot, REST API
  outbound/                      ← LLM providers, tools, skills, repos, embedding
infrastructure/                  ← Wiring & cross-cutting
  container.py                   ← DI containers (AgentContainer, AppContainer) — ONLY place adapters are instantiated
  config.py                      ← Pydantic config models + 4-layer YAML merge
  factories/                     ← LLM and embedding provider factories
ext/                             ← User extensions (auto-discovered via manifest.py)
```

**Key boundary**: `infrastructure/container.py` is the ONLY place where concrete adapters are wired. Adding a new tool, provider, or repo means registering it here.

## Configuration System

Config lives in `~/.inaki/` by default (NOT in the repo tree). First run bootstraps it.

**4-layer merge** (each layer overrides only fields it defines):
1. `~/.inaki/config/global.yaml`
2. `~/.inaki/config/global.secrets.yaml`
3. `~/.inaki/agents/{id}.yaml`
4. `~/.inaki/agents/{id}.secrets.yaml`

Override with `--config DIR` to use a custom directory.

**Critical**: `*.secrets.yaml` files are in `.gitignore`. Never commit them. Agent registry skips files with `.example` in the name.

## LLM & Embedding Providers

**LLM**: Auto-discovered. Add `adapters/outbound/providers/{name}.py` with `PROVIDER_NAME = "{name}"`. Built-in: `openrouter`, `openai`, `ollama`, `groq`.

**Embedding**: `e5_onnx` (local ONNX, ARM64-compatible for Pi 5). Dimension is 384 — changing it requires dropping and recreating the memory DB.

## Memory & History

Two separate SQLite databases:
- `data/inaki.db` — long-term memory with `sqlite-vec` vector embeddings
- `data/history.db` — short-term chat history (sliding window)

Consolidation extracts memories from chat history via LLM, embeds them, stores in vector DB, then writes a markdown digest to `~/.inaki/mem/last_memories.md`.

## Agent Delegation

Two-phase init in `AppContainer`:
1. Build all `AgentContainer` instances
2. Wire delegation (register `delegate` tool) after all containers exist

Recursion prevention is structural: the `delegate` tool is filtered from sub-agent schemas. `max_depth` does not exist as a config field.

## Extensions

User extensions live in `ext/` or `~/.inaki/ext/`. Each extension needs a `manifest.py` exposing `TOOLS` (list of classes) and `SKILLS` (list of relative file paths). The parent directory is added to `sys.path` so extension-internal imports resolve.

## Testing

- `pytest-asyncio` mode is `"auto"` — no `@pytest.mark.asyncio` needed
- Fixtures in `tests/conftest.py`: `agent_config`, `mock_llm`, `mock_memory`, `mock_embedder`, `mock_skills`, `mock_history`, `mock_tools`
- `agent_config` fixture uses `:memory:` for DB path — safe for unit tests
- Integration tests require real SQLite files (check `tests/integration/`)

## OpenSpec Workflow

Changes follow: `openspec/changes/{name}/` → proposal → spec → design → tasks → apply → verify → archive to `openspec/changes/archive/`.

Use the `sdd-*` skills for this workflow.

## Production Target

Deployed on Raspberry Pi 5 (4GB RAM) via systemd (`systemd/inaki.service`). Memory limit 2G. Production paths: `/home/pi/inaki/` for code, `/home/pi/inaki/data/` for DBs, `/home/pi/.inaki/` for config.

---

## References

- **Tech Spec**: `docs/inaki_spec_v2.md`
- **Architecture**: `docs/estructura.md`
- **Execution Flow**: `docs/flujo_ejecucion.md`
- **Config Reference**: `docs/configuracion.md`
- **Scheduler Spec**: `docs/scheduler-spec.md`
- **GitHub**: https://github.com/alberto2112/inaki