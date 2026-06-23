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
- **Extensions** — drop a `manifest.py` in `ext/` and the tools/skills are auto-discovered

---

## Architecture

Inaki follows **strict hexagonal (Ports & Adapters)** architecture:

```
ext/          ← User extensions (auto-discovered)
adapters/
  inbound/    ← CLI · Telegram · REST · daemon
  outbound/   ← LLM providers · tools · memory · embeddings · skills · scheduler
core/
  domain/     ← Entities, value objects, errors — zero external imports
  ports/      ← Interfaces (inbound + outbound)
  use_cases/  ← RunAgentUseCase · ConsolidateMemoryUseCase · ScheduleTaskUseCase
infrastructure/
  container.py   ← Single wiring point (DI composition root)
  config.py      ← 4-layer YAML loader
```

**Dependency direction is inviolable:** `adapters/ → core/`. The `core/` layer never imports from `adapters/` or any infrastructure library. `infrastructure/container.py` is the only place where concrete adapters are instantiated.

---

## Requirements

- Python 3.11+
- Raspberry Pi 5 recommended (works on any ARM64 or x86-64 Linux machine)
- For embeddings: ONNX model files (`intfloat/multilingual-e5-small`) placed in `~/.inaki/models/e5-small/`
- For face recognition: InsightFace (~400MB RAM when loaded, lazy-loaded on first photo)

---

## Installation

```bash
git clone https://github.com/alberto2112/inaki.git
cd inaki
pip install -e ".[dev]"
```

This installs the `inaki` CLI entrypoint.

---

## Configuration

All user data lives in **`~/.inaki/`** (never inside the repo). On first run, the directory and a starter config are bootstrapped automatically.

```
~/.inaki/
├── config/
│   ├── global.yaml              # Global defaults (LLM, memory, embedding…)
│   ├── global.secrets.yaml      # API keys — gitignored, never commit this
│   ├── tool_config.yaml         # Tool credentials (daemon-owned)
│   └── agents/
│       ├── general.yaml         # Agent-specific overrides
│       └── general.secrets.yaml # Agent secrets (Telegram token, etc.)
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

Config is resolved via a **4-layer YAML merge** (each layer overrides only what it defines):

```
global.yaml  →  global.secrets.yaml  →  agents/{id}.yaml  →  agents/{id}.secrets.yaml
```

`tool_config.yaml` is **not part of this merge** — it is daemon-owned (written at runtime when tools store credentials) and read directly by the `YamlToolConfigStore`. Sensitive fields are stored with Fernet encryption (`enc:` prefix) using `~/.inaki/secret.key`.

See [`config/global.example.yaml`](config/global.example.yaml) for the full annotated reference — every parameter is documented there.

### Minimal config example

`~/.inaki/config/global.yaml`:
```yaml
app:
  default_agent: general

providers:
  openrouter: {}   # api_key goes in global.secrets.yaml

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

`~/.inaki/config/global.secrets.yaml`:
```yaml
providers:
  openrouter:
    api_key: "sk-or-..."
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
inaki setup                      # Interactive TUI for editing config (offline)
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
# agents/general.yaml
channels:
  telegram:
    allowed_user_ids: ["123456789"]   # Allowed private chat user IDs. Empty = everyone.
    allowed_chat_ids: []              # Allowed group chat IDs (negative numbers).
                                      # Empty list = bot does NOT respond in groups.
    reactions: true
    voice_enabled: true               # Whisper transcription for voice messages
    # token → agents/general.secrets.yaml
```

```yaml
# agents/general.secrets.yaml
channels:
  telegram:
    token: "7xxxxxxx:AAF..."
```

### REST API

All HTTP surface lives on the admin server (single port, routed by `agent_id`, auth via `X-Admin-Key`):

`POST /admin/chat/turn` — send a message and get a response.  
`GET /admin/agents` — registered agent ids · `GET /admin/agent/info` — agent metadata.  
`GET /admin/chat/history` — conversation history.

### Multi-Pi broadcast

Multiple Inaki instances on the same LAN can share a Telegram group conversation via a HMAC-signed TCP side-channel (the Bot API does not deliver messages from other bots). One instance acts as the server (`broadcast.port`), others as clients (`broadcast.remote`). See [`docs/configuracion.md`](docs/configuracion.md) and [`docs/broadcast-smoke.md`](docs/broadcast-smoke.md).

---

## Extensions

Drop a folder in `ext/` with a `manifest.py` and your tools/skills are auto-discovered at startup. No registration needed.

```
ext/
├── my_extension/
│   ├── manifest.py       # Declares package path for discovery
│   ├── my_tool.py        # Implements ITool
│   └── my_skill.yaml     # Skill instructions injected in system prompt
```

Included extensions: `exchange_calendar`, `nominatim`, `notes_todo_list`, `replicate_music`, `shell_exec`.

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

See [`ext/USER.md`](ext/USER.md) and [`docs/tools_y_skills.md`](docs/tools_y_skills.md) for conventions.

---

## Deployment on Raspberry Pi 5

```bash
# Install systemd service
sudo bash systemd/install.sh

# Start / stop / status
sudo systemctl start inaki
sudo systemctl stop inaki
sudo systemctl status inaki

# View logs
journalctl -u inaki -f
```

The service file is at [`systemd/inaki.service`](systemd/inaki.service). It runs the `inaki daemon` command which starts all configured agents and channels.

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
| [`docs/inaki_spec.md`](docs/inaki_spec.md) | Full technical spec |
| [`docs/flujo_ejecucion.md`](docs/flujo_ejecucion.md) | Execution flow and bootstrap |
| [`docs/configuracion.md`](docs/configuracion.md) | Config reference |
| [`docs/scheduler-spec.md`](docs/scheduler-spec.md) | Scheduler design |
| [`docs/face-recognition.md`](docs/face-recognition.md) | Face recognition pipeline |
| [`docs/broadcast-smoke.md`](docs/broadcast-smoke.md) | Multi-Pi broadcast smoke test |
| [`CHANGELOG.md`](CHANGELOG.md) | Release history |

---

## License

[Polyform Noncommercial License 1.0.0](LICENSE) — free to use, clone, and modify for any non-commercial purpose. Commercial use is not permitted.
