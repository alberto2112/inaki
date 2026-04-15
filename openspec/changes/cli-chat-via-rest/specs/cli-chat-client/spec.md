# CLI Chat Client Specification

## Purpose

Specifies the behavior of the `inaki chat` command after the hard cutover to daemon-only mode. The CLI MUST act as a thin HTTP client, delegating all agent execution to the daemon via the admin REST API.

---

## Requirements

### Requirement: Session UUID generation

The CLI MUST generate a UUID at process startup and use it as `session_id` for all requests in that process lifetime. The UUID MUST NOT be persisted across restarts.

#### Scenario: UUID generated per process

- GIVEN the user runs `inaki chat`
- WHEN the process starts
- THEN a UUID is generated and held in memory
- AND every subsequent `POST /admin/chat/turn` uses that UUID as `session_id`

---

### Requirement: Daemon reachability check at startup

The CLI MUST verify the daemon is reachable before entering the interactive loop. If the daemon is unreachable, the CLI MUST exit with a clear error message pointing to `inaki daemon`.

#### Scenario: Daemon unreachable at startup

- GIVEN the daemon is not running on port 6497
- WHEN the user runs `inaki chat`
- THEN the CLI prints an actionable error: "El daemon no está corriendo. Inicialo con `inaki daemon`."
- AND the process exits with a non-zero exit code (no interactive loop entered)

---

### Requirement: Sending a message turn

On each user input, the CLI MUST call `POST /admin/chat/turn`, display the response, and remain in the interactive loop. The `--agent <id>` flag MUST be passed in every request body.

#### Scenario: Happy path — user sends a message

- GIVEN the CLI is in the interactive loop with agent `dev`
- AND the daemon is running
- WHEN the user types a non-empty message and presses Enter
- THEN the CLI POSTs `{"agent_id": "dev", "session_id": "<uuid>", "message": "<text>"}` to `/admin/chat/turn`
- AND the CLI prints the assistant response
- AND the CLI returns to the input prompt

#### Scenario: Daemon becomes unreachable mid-session

- GIVEN the CLI is in the interactive loop
- AND the daemon stops responding (timeout or connection refused)
- WHEN the user submits a message
- THEN the CLI prints an error message (e.g., "No se pudo contactar al daemon. ¿Reintentás? [s/n]")
- AND if user chooses retry, the CLI attempts the same request again
- AND the CLI does NOT crash or exit without user confirmation

#### Scenario: User presses Ctrl+C

- GIVEN the CLI is in the interactive loop
- WHEN the user presses Ctrl+C (whether during input or while waiting for a response)
- THEN the CLI exits cleanly with exit code 0
- AND no partial request is retried

---

### Requirement: /clear command

The CLI MUST recognize `/clear` as a special command that calls `DELETE /admin/chat/history` for the current `agent_id` and prints confirmation.

#### Scenario: User clears history

- GIVEN the CLI is in the interactive loop
- WHEN the user types `/clear` and presses Enter
- THEN the CLI calls `DELETE /admin/chat/history?agent_id=<current>`
- AND on 204 response, prints "Historial limpiado."
- AND the next message starts a fresh conversation

---

### Requirement: /exit and /quit commands

The CLI MUST recognize `/exit` and `/quit` as commands that terminate the interactive loop cleanly.

#### Scenario: User types /exit or /quit

- GIVEN the CLI is in the interactive loop
- WHEN the user types `/exit` or `/quit`
- THEN the CLI exits with code 0 without sending any request to the daemon

---

### Requirement: Session and history semantics

The session_id is used by the server to set `ChannelContext.user_id`. The history is stored per `agent_id` only — the session_id has NO effect on history segmentation.

#### Scenario: Same session_id across turns = same conversation context

- GIVEN a CLI process with session_id `S1` sends turns T1 and T2 to agent `dev`
- WHEN the server processes T2
- THEN the server includes T1 in the conversation context (same ChannelContext lookup)

#### Scenario: Different session_ids share agent history

- GIVEN two CLI processes with session_ids `S1` and `S2` both interact with agent `dev`
- WHEN either process calls `GET /admin/chat/history?agent_id=dev`
- THEN both processes see each other's turns in the history
- AND `DELETE /admin/chat/history` from either process clears for both

#### Scenario: Telegram and CLI share agent history

- GIVEN a Telegram user and a CLI process both interact with agent `dev`
- WHEN either calls `DELETE /admin/chat/history` (or Telegram `/clear`)
- THEN both see an empty history on the next interaction
- AND turns from Telegram appear in CLI history and vice versa

---

### Requirement: /agents command — list registered agents

The CLI MUST recognize `/agents` as a special command that calls `GET /admin/agents` and prints the list of registered agent IDs.

#### Scenario: User types /agents — daemon responds with agents

- GIVEN the CLI is in the interactive loop
- AND the daemon is running with agents `dev` and `general` registered
- WHEN the user types `/agents`
- THEN the CLI calls `GET /admin/agents`
- AND prints the list of agent IDs

#### Scenario: /agents — daemon unreachable

- GIVEN the CLI is in the interactive loop
- AND the daemon is not responding
- WHEN the user types `/agents`
- THEN the CLI prints an error message and remains in the loop (non-fatal)
