# Inaki

Personal AI assistant designed to run as a systemd service on a **Raspberry Pi 5 (4GB RAM, ARM64)**. Multi-agent, multi-channel, with long-term memory and a strict hexagonal architecture.

---

## Features

- **Multi-agent** — define multiple agents with independent configs, LLM providers, and personalities
- **Multi-channel** — CLI, Telegram bot, and REST API simultaneously on the same daemon
- **Long-term memory** — per-scope RAG (SQLite + sqlite-vec) with nightly LLM-powered consolidation and optional memory reconciliation (resolves contradictions and merges outdated facts automatically)
- **Semantic routing** — tools and skills are selected via embedding similarity, not hardcoded lists
- **Scheduler** — one-shot and recurring tasks (cron) with a built-in TUI and CLI
- **Agent delegation** — agents can delegate to other agents synchronously or in the background
- **Face recognition** — optional InsightFace pipeline for photo processing in Telegram (lazy-loaded)
- **Voice transcription** — Whisper-based transcription for Telegram voice messages
- **Knowledge sources** — RAG over local documents (Markdown, PDF) with configurable chunking
- **Multi-Pi broadcast** — multiple Inaki instances on the same LAN can collaborate in a shared Telegram group via HMAC-signed TCP
- **Extensions** — drop a `manifest.py` in `~/.inaki/ext/` and the tools/skills are auto-discovered

---

## Architecture

Inaki follows **strict hexagonal (Ports & Adapters)** architecture:

```
inaki/
  shared/       ← Domain primitives shared by every module (Message, attachments, errors)
  observability/← Logging, debug mode, turn traces
  config/       ← Schema (one section per area), 2-layer YAML loader, merge engine
  channels/     ← One package per channel: telegram (bot, outbound, broadcast, files), rest (admin API), cli (chat)
  cli/          ← Entry points (one module per command)
  app/          ← Bootstrap, daemon runner, reloader
  llm/          ← LLM providers (OpenAI-compatible family, Anthropic, Ollama, Responses)
  tools/        ← ToolRegistry with semantic routing, builtin tools, tool-config store
  extensions/   ← Discovery of user extensions (`<home>/ext/*/manifest.py`)
  scheduler/    ← Scheduled tasks: domain, ports, service, SQLite repo, the `scheduler` tool (one object per operation)
  agents/       ← Delegation (`delegate` tool, background queue), per-scope dispatcher, scope registry
  embedding/ memory/ knowledge/ skills/ perception/ ← Feature modules (each with its adapters, use cases and tools)
  kernel/       ← The turn: RunAgentUseCase, tool loop, the ports it consumes, the channel contract
```

A modular monolith under a single namespace: kernel + feature modules + channels + composition root (`inaki/app`, `inaki/cli`). Dependency rules are enforced by `lint-imports` (see `[tool.importlinter]` in `pyproject.toml`).

**Dependency direction is inviolable:** `composition root → modules → kernel`. The kernel never imports a feature module; modules only know the kernel, `inaki/shared` and what their contract allows (a module's `wiring.py` is the only file allowed to read the config). `inaki/app/assembly.py` calls each module's `wiring.py` in five explicit passes and hands back immutable, typed runtimes.

---

## Requirements

- Python 3.11+
- Raspberry Pi 5 recommended (works on any ARM64 or x86-64 Linux machine)
- For embeddings: ONNX model files (`intfloat/multilingual-e5-small`) in `~/.inaki/models/e5-small/` — `inaki init` downloads them
- For face recognition: InsightFace (~400MB RAM when loaded, lazy-loaded on first photo)

---

## Installation

Inaki is a regular Python package with an `inaki` console script. On the Pi (or anywhere), install it isolated with [pipx](https://pipx.pypa.io/) and let the wizard do the rest:

```bash
pipx install "git+https://github.com/alberto2112/inaki"   # add [faces] for face recognition
inaki init                                                  # provider, first agent, Telegram, embeddings model
inaki service install                                       # systemd unit (prints the sudo steps)
```

`inaki init` asks a handful of sequential questions, writes `~/.inaki/config/global.yaml` and the first agent YAML through the same config use cases every other editor uses, validates the result with the **same loader the daemon boots with**, and offers to download the local embeddings model (`intfloat/multilingual-e5-small`, ~470 MB) into `~/.inaki/models/e5-small/`. Re-running it is safe: it asks before replacing a credential or creating another agent.

Face recognition (`photos.enabled`) needs `insightface`, which compiles on ARM, so it is an **optional extra**: `pipx install "inaki[faces] @ git+https://github.com/alberto2112/inaki"`. With photos enabled and the extra missing, the daemon starts without photos and logs an `ERROR` saying so.

For development, clone and install editable:

```bash
git clone https://github.com/alberto2112/inaki.git
cd inaki
pip install -e ".[dev]"
```

## Configuration

All user data lives in **`~/.inaki/`** (never inside the repo). On first run, the directory and a starter config are bootstrapped automatically.

```
~/.inaki/
├── config/
│   ├── global.yaml              # Global defaults (LLM, memory, embedding…) + API keys
│   │                            # mode 600 — never commit this
│   ├── tool_config.yaml         # Tool credentials (daemon-owned)
│   └── agents/
│       └── general.yaml         # Agent-specific overrides + its tokens
│                                # mode 600 — never commit this either
├── data/
│   ├── inaki.db                 # Long-term memory (SQLite + sqlite-vec)
│   ├── history.db               # Conversation history
│   └── faces.db                 # Face recognition DB (created on first photo)
├── models/
│   └── e5-small/                # ONNX embedding model
│       ├── model.onnx
│       └── tokenizer.json
└── mem/                         # Markdown memory digests (per scope)
```

### Config merging

Config is resolved via a **2-layer YAML merge** (each layer overrides only what it defines):

```
global.yaml  →  agents/{id}.yaml
```

Credentials (provider `api_key`s, Telegram tokens, `admin.auth_key`) live in those same two files, which are created with mode `600` — **neither is committable**. "Secret" is a mark in the Pydantic schema, not a separate file: it is what makes `inaki config show` redact the field. Installs coming from the old `*.secrets.yaml` sidecars are migrated automatically on first start; no operator action needed.

`tool_config.yaml` is **not part of this merge** — it is daemon-owned (written at runtime when tools store credentials) and read directly by the `YamlToolConfigStore`. Sensitive fields are stored with Fernet encryption (`enc:` prefix) using `~/.inaki/secret.key`.

See [`config/global.example.yaml`](config/global.example.yaml) for the full annotated reference — every parameter is documented there.

### Minimal config example

`~/.inaki/config/global.yaml`:
```yaml
app:
  default_agent: general

providers:
  openrouter:
    api_key: "sk-or-..."   # credential — this file is never committed

llm:
  provider: openrouter
  model: anthropic/claude-3-5-haiku
  temperature: 0.7
  max_tokens: 2048

embedding:
  provider: e5_onnx
  model_dirname: models/e5-small
  dimension: 384
```

`~/.inaki/config/agents/general.yaml`:
```yaml
id: general
name: Inaki
description: Personal general-purpose assistant
system_prompt: |
  You are Inaki, a personal AI assistant.

memories:
  consolidation:
    enabled: true
```

---

## Usage

```bash
inaki                            # Interactive CLI (default agent)
inaki chat --agent dev           # Interactive CLI with a specific agent
inaki chat --agent list          # List all configured agents
inaki daemon                     # Start all agents and all channels (systemd mode)
inaki reload                     # Hot-reload daemon (closes channels, reloads config, restarts)
inaki consolidate                # Run memory consolidation for all agents
inaki consolidate --agent dev    # Consolidate a single agent
inaki inspect "query"            # Inspect RAG pipeline for a message (no LLM call)
inaki scheduler list             # List scheduled tasks
inaki knowledge list             # List configured knowledge sources
```

### Remote mode

```bash
inaki --remote http://raspi.local:6497 chat
inaki --remote http://raspi.local:6497 --remote-key MY_KEY chat
```

Or set `INAKI_REMOTE` env var to avoid typing the flag every time.

---

## Channels

Each agent can expose multiple inbound channels simultaneously. Channels are configured per-agent in `agents/{id}.yaml` under the `channels:` key.

### Telegram

```yaml
# agents/general.yaml  (mode 600 — never commit it, the token lives here)
channels:
  telegram:
    token: "7xxxxxxx:AAF..."          # Bot token from BotFather
    allowed_user_ids: ["123456789"]   # Allowed private chat user IDs. Empty = everyone.
    allowed_chat_ids: []              # Allowed group chat IDs (negative numbers).
                                      # Empty list = bot does NOT respond in groups.
    reactions: true
    voice_enabled: true               # Whisper transcription for voice messages
```

### REST API

All HTTP surface lives on the admin server (single port, routed by `agent_id`, auth via `X-Admin-Key`):

`POST /admin/chat/turn` — send a message and get a response.  
`GET /admin/agents` — registered agent ids · `GET /admin/agent/info` — agent metadata.  
`GET /admin/chat/history` — conversation history.

### Multi-Pi broadcast

Multiple Inaki instances on the same LAN can share a Telegram group conversation via a HMAC-signed TCP side-channel (the Bot API does not deliver messages from other bots). One instance acts as the server (`broadcast.port`), others as clients (`broadcast.remote`). See [`docs/broadcast-smoke.md`](docs/broadcast-smoke.md).

---

## Extensions

Drop a folder in `~/.inaki/ext/` (`app.ext_dirs`) with a `manifest.py` and your tools/skills are auto-discovered at startup. No registration needed.

```
~/.inaki/ext/
├── my_extension/
│   ├── manifest.py       # Declares package path for discovery
│   ├── my_tool.py        # Implements ITool
│   └── my_skill.yaml     # Skill instructions injected in system prompt
```

### Tool Config Protocol

Tools that need user-configured credentials (API keys, passwords, etc.) can opt into the Tool Config Protocol by declaring a `config_namespace` class attribute. The container automatically injects a `config_store: IToolConfigStore` so the tool can persist and retrieve its settings conversationally:

```python
class MyTool(ITool):
    config_namespace = "my_tool"   # namespace in tool_config.yaml

    def __init__(self, config_store: IToolConfigStore):
        self._store = config_store

    async def execute(self, ...):
        cfg = self._store.get(self.config_namespace)
        # cfg["api_key"] etc.
```

The agent can then configure credentials at runtime ("set my_tool api_key to …"), which are encrypted at rest in `~/.inaki/config/tool_config.yaml` and survive daemon restarts. No separate YAML file or `CryptoService` needed.

See [`docs/tools_y_skills.md`](docs/tools_y_skills.md) for conventions.

---

## Deployment on Raspberry Pi 5

```bash
inaki service install          # generates the unit; without root it prints the exact sudo steps
sudo systemctl status inaki
journalctl -u inaki -f
inaki service uninstall
```

`inaki service install` renders `/etc/systemd/system/inaki.service` from a template shipped **inside the package**, with the absolute path of the `inaki` executable that ran the command (a repo venv or a pipx venv alike), the invoking user and group, and `Environment=INAKI_HOME=<home>` so the daemon runs the same instance your shell sees. It never depends on `PATH`: systemd does not read `.bashrc` or `.profile`. Run without root it writes the unit to `~/.inaki/inaki.service` and prints the three `sudo` commands; run with `sudo` (use the full path to the executable, e.g. `sudo ~/.local/pipx/venvs/inaki/bin/inaki service install`) it installs, enables and restarts the service itself.

`--print` only shows the rendered unit. `--link-cli` additionally symlinks the CLI to `/usr/local/bin/inaki`. That is opt-in on purpose: `/usr/local/bin` is in systemd's minimal `PATH`, so the link makes the whole harness (`inaki scheduler`, `inaki knowledge`, `inaki tool`, …) reachable from the agent's `shell_exec` tool. Prefer capabilities as tools; `shell_exec` reaching for the CLI is usually a smell. Your login shell already finds `inaki` through pipx's `~/.local/bin` (or the venv), no link needed.

Running a second, isolated instance: install the unit under another name with its own home (`inaki --home /srv/inaki-b service --print > inaki-b.service`, then edit `admin.port` / `broadcast.port` in that home's YAML so they do not collide).

---

## Development

```bash
pip install -e ".[dev]"

ruff check .          # Lint
ruff format .         # Format (line-length 100)
mypy .                # Type check

pytest                        # All tests
pytest tests/unit/            # Unit tests only
pytest tests/integration/     # Integration tests only
pytest -k test_name           # Single test
```

`pytest-asyncio` is configured in `auto` mode — no `@pytest.mark.asyncio` decorator needed.

Shared fixtures in [`tests/conftest.py`](tests/conftest.py): `agent_config`, `mock_llm`, `mock_memory`, `mock_embedder`, `mock_skills`, `mock_history`, `mock_tools`.

---

## Documentation

| Doc | What it covers |
|-----|---------------|
| [`docs/arquitectura.md`](docs/arquitectura.md) | Structural rules: layers, thin channel, resource tiers, wiring |
| [`docs/flujo_ejecucion.md`](docs/flujo_ejecucion.md) | A turn end to end, plus startup and bootstrap |
| [`docs/configuracion.md`](docs/configuracion.md) | Config — index and entry point |
| [`docs/config-reference.md`](docs/config-reference.md) | Config — every field (autogenerated) |
| [`docs/scheduler-spec.md`](docs/scheduler-spec.md) | Scheduler design |
| [`docs/face-recognition.md`](docs/face-recognition.md) | Face recognition pipeline |
| [`docs/broadcast-smoke.md`](docs/broadcast-smoke.md) | Multi-Pi broadcast smoke test |
| [`CHANGELOG.md`](CHANGELOG.md) | Release history |

---

## License

[Polyform Noncommercial License 1.0.0](LICENSE) — free to use, clone, and modify for any non-commercial purpose. Commercial use is not permitted.
