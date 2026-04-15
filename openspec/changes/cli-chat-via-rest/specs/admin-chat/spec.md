# Admin Chat Endpoints Specification

## Purpose

Specifies the three REST endpoints added to the admin server (port 6497) that enable turn-based CLI chat via the daemon. All endpoints require `X-Admin-Key` authentication and are stateless on the server side.

---

## Requirements

### Requirement: POST /admin/chat/turn — Send a message turn

The endpoint MUST accept a JSON body with `agent_id`, `session_id`, and `message`, invoke `run_agent.execute()` for the named agent using a `ChannelContext(channel_type="cli", user_id=session_id)`, and return the complete assistant response.

#### Scenario: Happy path — valid turn

- GIVEN the daemon is running with agent `dev` registered
- AND the client sends `X-Admin-Key: <valid-key>`
- WHEN the client POSTs `{"agent_id": "dev", "session_id": "<uuid>", "message": "hola"}` to `/admin/chat/turn`
- THEN the response status is 200
- AND the body contains `{"response": "<assistant text>", "turn_count": 1}`
- AND the turn is persisted in the shared HistoryRepo for agent `dev`

#### Scenario: Missing or invalid X-Admin-Key

- GIVEN the daemon is running
- WHEN the client POSTs to `/admin/chat/turn` with a missing or wrong `X-Admin-Key`
- THEN the response status is 401

#### Scenario: Unknown agent_id

- GIVEN the daemon is running and agent `ghost` is not registered
- WHEN the client POSTs `{"agent_id": "ghost", "session_id": "<uuid>", "message": "hola"}`
- THEN the response status is 404
- AND the body contains a structured error with `error_code: "agent_not_found"`

#### Scenario: Missing session_id

- GIVEN the daemon is running
- WHEN the client POSTs `{"agent_id": "dev", "message": "hola"}` (no `session_id` field)
- THEN the response status is 400
- AND the body contains a structured error indicating the missing field

#### Scenario: Missing or empty message

- GIVEN the daemon is running
- WHEN the client POSTs `{"agent_id": "dev", "session_id": "<uuid>", "message": ""}` or omits `message`
- THEN the response status is 400

#### Scenario: Daemon internal error during run_agent.execute()

- GIVEN the daemon is running with agent `dev`
- AND `run_agent.execute()` raises an unhandled exception
- WHEN the client POSTs a valid turn request
- THEN the response status is 500
- AND the body contains `{"error": "<description>", "error_code": "internal_error"}`

#### Scenario: Tool loop reaches iteration limit

- GIVEN the daemon is running with agent `dev`
- AND the tool loop hits `tool_call_max_iterations`
- WHEN the client POSTs a valid turn request
- THEN the response status is 200
- AND the body contains the last available assistant response (matches current tool loop behavior — no error raised)

---

### Requirement: GET /admin/chat/history — Retrieve agent history

The endpoint MUST return the ordered list of messages from the shared HistoryRepo for the given `agent_id`, using `history.load()` (not `load_full()`). An empty history MUST return 200 with an empty list, not 404.

#### Scenario: Happy path — history with messages

- GIVEN agent `dev` has two prior turns in its HistoryRepo
- AND the client sends a valid `X-Admin-Key`
- WHEN the client GETs `/admin/chat/history?agent_id=dev`
- THEN the response status is 200
- AND the body contains `{"messages": [{"role": "user", "content": "...", "timestamp": "..."}, ...]}`
- AND messages appear in chronological order

#### Scenario: Empty history

- GIVEN agent `dev` exists but has no history
- WHEN the client GETs `/admin/chat/history?agent_id=dev`
- THEN the response status is 200
- AND the body contains `{"messages": []}`

#### Scenario: Unknown agent_id

- GIVEN agent `ghost` is not registered
- WHEN the client GETs `/admin/chat/history?agent_id=ghost`
- THEN the response status is 404

#### Scenario: Missing auth

- WHEN the client GETs `/admin/chat/history?agent_id=dev` without `X-Admin-Key`
- THEN the response status is 401

---

### Requirement: DELETE /admin/chat/history — Clear agent history

The endpoint MUST clear ALL history for the given `agent_id` from the shared HistoryRepo and return 204. This operation affects all channels (CLI and Telegram) that share the same agent history.

#### Scenario: Happy path — clear existing history

- GIVEN agent `dev` has history in its HistoryRepo
- AND the client sends a valid `X-Admin-Key`
- WHEN the client sends `DELETE /admin/chat/history?agent_id=dev`
- THEN the response status is 204
- AND subsequent GET to `/admin/chat/history?agent_id=dev` returns `{"messages": []}`

#### Scenario: Unknown agent_id

- GIVEN agent `ghost` is not registered
- WHEN the client sends `DELETE /admin/chat/history?agent_id=ghost`
- THEN the response status is 404

#### Scenario: Missing auth

- WHEN the client sends `DELETE /admin/chat/history?agent_id=dev` without `X-Admin-Key`
- THEN the response status is 401

#### Scenario: Fresh turn after DELETE starts clean

- GIVEN the client has cleared history via DELETE
- WHEN the client POSTs a new turn for the same `agent_id`
- THEN the response contains `"turn_count": 1`
- AND no previous messages appear in subsequent GET history

#### Scenario: Cross-channel effect — Telegram sees cleared history

- GIVEN a Telegram user and a CLI client both interact with agent `dev`
- WHEN the CLI client calls `DELETE /admin/chat/history?agent_id=dev`
- THEN the Telegram user's next interaction starts from an empty history
- AND (equivalently) a `/clear` from Telegram also clears what the CLI sees

---

### Requirement: GET /admin/agents — List registered agents

The endpoint MUST return the list of agent IDs registered in the daemon's `AppContainer`.

#### Scenario: Happy path — agents registered

- GIVEN the daemon is running with agents `dev` and `general` registered
- AND the client sends a valid `X-Admin-Key`
- WHEN the client GETs `/admin/agents`
- THEN the response status is 200
- AND the body contains `{"agents": ["dev", "general"]}`

#### Scenario: Missing auth

- WHEN the client GETs `/admin/agents` without `X-Admin-Key`
- THEN the response status is 401

---

### HistoryMessage schema — field reference

| Field | Type | Description |
|-------|------|-------------|
| `role` | string | Rol del mensaje: `user`, `assistant`, `system`, `tool` |
| `content` | string | Contenido del mensaje |
| `timestamp` | string (ISO 8601) or null | Marca de tiempo del mensaje. Puede ser null si el mensaje fue creado sin timestamp. |
