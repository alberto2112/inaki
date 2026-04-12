# Delta for Scheduler-Internal

## ADDED Requirements

### Requirement: Webhook Trigger Payload (FR-13)

The system MUST support a `webhook` trigger type that performs an outbound HTTP request to an external URL. The payload MUST include:

| Field | Type | Required | Default |
|-------|------|----------|---------|
| `url` | `str` | Yes | — |
| `method` | `str` | No | `"POST"` |
| `headers` | `dict[str, str]` | No | `{}` |
| `body` | `str \| None` | No | `None` |
| `timeout` | `int` | No | `30` |
| `success_codes` | `list[int]` | No | `[200, 201, 202, 204]` |

The `type` discriminator field MUST be `"webhook"`.

#### Scenario: Valid webhook payload accepted

- GIVEN a task is created with `trigger_type = "webhook"` and payload `{"type": "webhook", "url": "https://example.com/hook", "method": "POST"}`
- WHEN the task is persisted
- THEN the payload is stored as valid JSON and retrievable intact

#### Scenario: Webhook payload with custom headers

- GIVEN a payload with `headers: {"Authorization": "Bearer tok123", "X-Custom": "value"}`
- WHEN the task is dispatched
- THEN the HTTP request includes both custom headers

#### Scenario: Webhook payload with defaults

- GIVEN a payload with only `url` specified
- WHEN the payload is validated
- THEN `method` defaults to `"POST"`, `headers` to `{}`, `body` to `null`, `timeout` to `30`, `success_codes` to `[200, 201, 202, 204]`

---

### Requirement: Webhook Dispatch (FR-14)

The system MUST dispatch `webhook` triggers by making an HTTP request using `httpx.AsyncClient`. The request MUST use the payload's `method`, `url`, `headers`, and `body`. The call MUST enforce `timeout` seconds via httpx timeout configuration.

If the response status code is NOT in `success_codes`, the system MUST raise an error, triggering the existing retry mechanism.

The response body text MUST be returned as the task output (subject to existing `output_truncation_size`).

#### Scenario: Successful webhook call

- GIVEN a webhook task with `url = "https://api.example.com/notify"` and `method = "POST"`
- WHEN the task is dispatched and the endpoint returns HTTP 200 with body `"ok"`
- THEN the task succeeds
- AND `task_logs.output` contains `"ok"`

#### Scenario: Non-success status code triggers failure

- GIVEN a webhook task with `success_codes = [200, 201]`
- WHEN the endpoint returns HTTP 500
- THEN the dispatch raises an error
- AND the existing retry mechanism is invoked

#### Scenario: Timeout exceeded

- GIVEN a webhook task with `timeout = 5`
- WHEN the endpoint does not respond within 5 seconds
- THEN the dispatch raises a timeout error
- AND `task_logs.error` records the timeout

#### Scenario: Connection refused

- GIVEN a webhook task targeting an unreachable host
- WHEN the task is dispatched
- THEN the dispatch raises a connection error
- AND the existing retry mechanism is invoked

---

## MODIFIED Requirements

### Requirement: Typed Trigger Payloads (FR-03)

The system MUST support exactly five trigger types. Each MUST carry a typed JSON payload stored in `trigger_payload`.
(Previously: supported exactly four trigger types)

| Trigger Type | Required Payload Fields |
|---|---|
| `channel.send_message` | `channel_id: str`, `message: str` |
| `agent.send_to_llm` | `prompt: str`, `output_channel: str \| null` |
| `shell_exec` | `command: str`, `timeout_seconds: int` |
| `cli_command` | `command: str`, `timeout_seconds: int` |
| `webhook` | `url: str`, `method: str`, `headers: dict`, `body: str \| null`, `timeout: int`, `success_codes: list[int]` |

#### Scenario: Valid channel.send_message payload accepted

- GIVEN a task is created with `trigger_type = "channel.send_message"` and payload `{"channel_id": "c1", "message": "hello"}`
- WHEN the task is persisted
- THEN the payload is stored as valid JSON and retrievable intact

#### Scenario: Valid webhook payload accepted

- GIVEN a task is created with `trigger_type = "webhook"` and payload `{"type": "webhook", "url": "https://example.com/hook"}`
- WHEN the task is persisted
- THEN the payload is stored as valid JSON and retrievable intact

#### Scenario: Unknown trigger type rejected

- GIVEN a task creation request with `trigger_type = "unknown_type"`
- WHEN the use case processes the request
- THEN `InvalidTriggerTypeError` is raised and no record is inserted

---

### Requirement: Trigger Dispatch (FR-07)

The system MUST dispatch each trigger type as follows:
(Previously: listed four dispatch rules; now five)

- `channel.send_message`: calls `channel.send_message(channel_id, message)`
- `agent.send_to_llm`: calls the LLM with the given `prompt`; if `output_channel` is set, sends result there; otherwise stores output in `task_logs.output`
- `shell_exec`: runs the command via `asyncio.create_subprocess_shell`; enforces `timeout_seconds` via `asyncio.wait_for`
- `cli_command`: runs the command via subprocess; enforces `timeout_seconds` via `asyncio.wait_for`
- `webhook`: makes an HTTP request to `url` with `method`, `headers`, and `body`; enforces `timeout` via httpx timeout; raises error if response status not in `success_codes`; returns response body as output

All dispatch calls MUST be async/awaited. Timeout violations MUST result in task `status = failed` and the error recorded in `task_logs.error`.

#### Scenario: agent.send_to_llm with no output_channel

- GIVEN a task with `trigger_type = "agent.send_to_llm"` and `output_channel = null`
- WHEN the task executes successfully
- THEN the LLM response is stored in `task_logs.output`
- AND no channel message is sent

#### Scenario: shell_exec with timeout exceeded

- GIVEN a task with `trigger_type = "shell_exec"` and `timeout_seconds = 5`
- WHEN the command runs longer than 5 seconds
- THEN `asyncio.wait_for` cancels the subprocess
- AND `status` is set to `failed`
- AND `task_logs.error` records a timeout message

#### Scenario: webhook with successful response

- GIVEN a task with `trigger_type = "webhook"` and `url = "https://api.example.com/hook"`
- WHEN the endpoint returns HTTP 200
- THEN the response body is stored in `task_logs.output`

#### Scenario: webhook with failed response

- GIVEN a task with `trigger_type = "webhook"` and `success_codes = [200]`
- WHEN the endpoint returns HTTP 503
- THEN the dispatch raises an error
- AND `task_logs.error` records the status code
