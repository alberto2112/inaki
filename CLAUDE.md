# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
pip install -e ".[dev]"          # install with dev deps
ruff check .                     # lint
ruff format .                    # format (line-length 100)
mypy .                           # type check
pytest                           # all tests
pytest tests/unit/               # unit only
pytest tests/integration/        # integration only
pytest -k test_name              # single test
inaki                            # interactive chat (default agent)
inaki chat --agent dev           # specific agent
inaki daemon                     # systemd service mode
```

No Makefile or CI. All commands are direct calls.

## Architecture

Iñaki is a multi-agent AI assistant following **strict hexagonal architecture**:

- **`core/`** — Domain layer. Entities, ports (interfaces), use cases, domain services and errors. **NEVER imports from `adapters/` or `infrastructure/`**. Only stdlib + `core/` imports allowed.
- **`adapters/`** — Concrete implementations of ports. Inbound (CLI, Telegram, REST, daemon) and outbound (LLM providers, tools, memory/history repos, embedding, skills, scheduler).
- **`infrastructure/`** — Wiring and cross-cutting. `container.py` is the **single place** where all adapters are instantiated and injected into use cases.
- **`ext/`** — User extensions auto-discovered via `manifest.py`.

Dependency direction: `adapters → core ←  infrastructure`. Never reversed.

### Key Wiring Rules

- **`infrastructure/container.py`** — `AgentContainer` (per-agent DI) and `AppContainer` (root, all agents). Registering a new tool, provider, or repo happens here and ONLY here.
- **Provider discovery** — LLM and embedding providers are auto-discovered by scanning modules for a `PROVIDER_NAME` module-level constant. No manual registration needed.
- **Two-phase agent init** — `AppContainer` first builds all `AgentContainer` instances, then wires delegation (the `delegate` tool) in a second pass so all containers exist before cross-references.

## Configuration

Config lives in **`~/.inaki/`** (not in the repo). First run bootstraps from `config/global.example.yaml`.

**4-layer YAML merge** (each layer overrides only fields it defines):
1. `~/.inaki/config/global.yaml`
2. `~/.inaki/config/global.secrets.yaml`
3. `~/.inaki/config/agents/{id}.yaml`
4. `~/.inaki/config/agents/{id}.secrets.yaml`

Secrets are YAML-only (no env vars). `*.secrets.yaml` files are gitignored.

## Testing

- `pytest-asyncio` mode is `"auto"` — no `@pytest.mark.asyncio` decorator needed
- Shared fixtures in `tests/conftest.py`: `agent_config` (uses `:memory:` DB), `mock_llm`, `mock_memory`, `mock_embedder`, `mock_skills`, `mock_history`, `mock_tools`
- Unit tests mock all adapters; integration tests use real SQLite

## Key Technical Details

- **Embedding dimension is 384** (e5-small ONNX). Changing models requires dropping and recreating the memory DB — no auto-migration.
- **All use cases** are classes with an async `execute()` method, injected via constructor in `container.py`.
- **Tool results** must be `ToolResult` objects, never raw strings.
- **Message roles** use `Role` enum (`Role.USER`, `Role.ASSISTANT`, etc.), not string literals.
- **Workspace containment** — `read_file`, `write_file` y `patch_file` usan `workspace.containment` (strict/warn/off). `shell_exec` NO tiene contención — opera en cualquier path. Ver `docs/configuracion.md`.
- **Tool loop** — LLM can call tools iteratively up to `tools.tool_call_max_iterations` (default 5) with a circuit breaker for repeated failures.
- **Codebase language** — Variables, docstrings, comments, and error messages are in Spanish.
- **Target platform** — Raspberry Pi 5 (ARM64, 4GB RAM) via systemd. See `systemd/inaki.service`.

## Migration Notes

### `multi-agent-telegram-broadcast`

The `history` table was extended with native `channel` and `chat_id` columns. No
auto-migration exists — the DB must be dropped and rebuilt.

Operator steps: stop daemon → `rm ~/.inaki/data/history.db ~/.inaki/data/inaki.db` → add
`channels.telegram.broadcast` config (optional) → restart. See `docs/broadcast-smoke.md`
for the full bootstrap walkthrough.

## Git workflow

- Never create a branch without asking me for the name first.
- Never commit without showing me the commit message for approval.
- Always ask before running `git merge` or `git push`.
- Preferred branch naming: `feature/`, `fix/`, `refactor/`, `experiment/`

## References

- **Tech Spec**: `docs/inaki_spec_v2.md`
- **Architecture**: `docs/estructura.md`
- **Execution Flow**: `docs/flujo_ejecucion.md`
- **Config Reference**: `docs/configuracion.md`
- **Scheduler Spec**: `docs/scheduler-spec.md`
- **Broadcast Smoke Test**: `docs/broadcast-smoke.md`
- **GitHub**: https://github.com/alberto2112/inaki
