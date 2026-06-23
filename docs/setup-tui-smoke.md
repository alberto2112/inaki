# Manual Smoke Test — `inaki setup` TUI V2 (Pi 5)

Manual verification checklist on Raspberry Pi 5 via SSH.
Run this list in a real environment after every release that touches the setup TUI.

---

## Prerequisites

- Inaki installed on the Pi 5 (systemd mode or direct venv).
- At least one agent configured in `~/.inaki/config/agents/`.
- Active SSH connection with a terminal of at least 80x24.

---

## 1. Access via SSH and Main Menu Screen

```bash
ssh pi@raspi.local
cd ~/inaki
source .venv/bin/activate
inaki setup
```

**Expected:** the TUI opens without error and shows `MainMenuPage` with 4 options:
`Global Config`, `Providers`, `Agents`, `Secrets`.

**If it fails:** verify `textual>=0.80` is installed (`pip show textual`). On Pi 5
with 4 GB RAM the TUI should open in under 3 seconds.

- [ ] TUI opens without traceback.
- [ ] Main menu is shown with the 4 categories.
- [ ] Breadcrumb in the top bar shows `inaki / setup`.

---

## 2. Welcome Modal (first launch)

When opening the TUI **for the first time** (without the `~/.inaki/setup_welcome_seen` flag):

- [ ] The "inaki setup — TUI" modal with welcome text is shown.
- [ ] The modal mentions `inaki setup secret-key` as an alternative for the Fernet wizard.
- [ ] Pressing `Enter` or `Esc` closes the modal.
- [ ] On subsequent opens it **does not** reappear.

To reset:

```bash
rm ~/.inaki/setup_welcome_seen
```

---

## 3. Keyboard Navigation — keyboard-first, no mouse

From `MainMenuPage`:

- [ ] `↓` / `j` moves the cursor down; the selected row is highlighted with the teal bar `▎`.
- [ ] `↑` / `k` moves the cursor up.
- [ ] `Enter` on "Global Config" opens `GlobalPage` (breadcrumb changes to `inaki / config / global`).
- [ ] `Esc` in `GlobalPage` returns to `MainMenuPage`.
- [ ] `q` in any screen exits cleanly.

---

## 4. GlobalPage — edit a global field

1. `Enter` on "Global Config" → `GlobalPage`.
2. Navigate with `↓` to any field in the `LLM` section (e.g. `model`).
3. Press `Enter` → `EditScalarModal` opens.
4. The modal shows the current value **pre-filled** in the input.
5. Change the value (e.g. `anthropic/claude-3-5-haiku` → `anthropic/claude-3-haiku`).
6. Press `Enter` to save.

**Verify in a separate shell:**

```bash
bat ~/.inaki/config/global.yaml | rg "model"
```

- [ ] The modal pre-fills the current value.
- [ ] The new value appears in `global.yaml`.
- [ ] **Comments from the original file are preserved** (ruamel.yaml).
- [ ] No YAML syntax errors.
- [ ] Notification "saved: model" appears in the status bar.

---

## 5. GlobalPage — `<null>` escape hatch

1. In `GlobalPage`, navigate to an optional field (e.g. `LLM → reasoning_effort`).
2. Press `Enter` → `EditScalarModal`.
3. Clear the content and type `<null>`.
4. Press `Enter`.

**Verify:**

```bash
bat ~/.inaki/config/global.yaml | rg "reasoning_effort"
```

- [ ] The field appears as `reasoning_effort: null` in the YAML (not absent, explicit `null`).

---

## 6. ProvidersPage — add a new provider

1. In `MainMenuPage`, Enter on "Providers" → `ProvidersPage`.
2. Press `n` (new provider) or the available binding.
3. Enter `id: test-provider`, `base_url: https://api.test.com/v1`.
4. Enter a test `api_key` (e.g. `sk-test-1234567890`).
5. Confirm.

**Verify:**

```bash
bat ~/.inaki/config/global.yaml | rg -A3 "test-provider"
bat ~/.inaki/config/global.secrets.yaml | rg "test-provider"
stat -f "%A" ~/.inaki/config/global.secrets.yaml   # should be 600
```

- [ ] `global.yaml` has the `test-provider` entry with `base_url`.
- [ ] `global.secrets.yaml` has the provider's `api_key`.
- [ ] Permissions on `global.secrets.yaml` are `600`.

---

## 7. AgentsPage — create a new agent

1. In `MainMenuPage`, Enter on "Agents" → `AgentsPage`.
2. Press the create binding (e.g. `n` or `c`).
3. Enter:
   - `id`: `smoke-test`
   - `name`: `Smoke Test Agent`
   - `description`: `Test agent for the TUI`
   - `system_prompt`: `You are a test agent.`
4. Confirm.

**Verify:**

```bash
bat ~/.inaki/config/agents/smoke-test.yaml
```

- [ ] The file exists with the 4 fields.
- [ ] It is valid YAML.

---

## 8. AgentDetailPage — editing flow with tri-state (memories.llm)

1. In `AgentsPage`, select `smoke-test` → Enter → `AgentDetailPage`.
2. Breadcrumb shows `inaki / config / agents / smoke-test`.
3. Navigate to the `MEMORY.LLM` section.
4. Select the `provider` field:
   - Press `Enter` → the tri-state modal opens with 3 options.
   - Select **Inherit** → save.
5. Select the `model` field:
   - Press `Enter` → tri-state modal.
   - Select **Own value** → enter `gpt-4o` → save.
6. Select the `temperature` field:
   - Press `Enter` → tri-state modal.
   - Select **Explicit null** → save.

**Verify:**

```bash
bat ~/.inaki/config/agents/smoke-test.yaml
```

- [ ] `memories.llm.provider` does NOT appear in the agent YAML (inherited).
- [ ] `memories.llm.model: gpt-4o` appears explicitly.
- [ ] `memories.llm.temperature: null` appears explicitly.

---

## 8b. Known-value fields are edited as a list

1. In `GlobalPage`, navigate to `LLM → provider`.
2. Press `Enter` → an `EditEnumModal` (list) opens, NOT a free-text input.
3. The options are the providers declared in `providers:`.

**Verify:**

- [ ] `llm.provider` opens a selectable list, not a text box.
- [ ] The listed options match the keys under `providers:` in `global.yaml`.
- [ ] A `Literal` field (e.g. `workspace → containment`) still lists its own
      schema options (`strict`/`warn`/`off`), unaffected by the provider list.

---

## 9. Cross-ref Validation — warning for invalid reference

1. In `GlobalPage`, navigate to `APP → default_agent`.
2. Press `Enter` → modal.
3. Enter a nonexistent id (e.g. `ghost-agent`).
4. Press `Enter` to save.

**Expected:**
- [ ] The value is saved (appears in YAML).
- [ ] A **warning** notification appears indicating an invalid reference (`app.default_agent`).
- [ ] The TUI does not crash or close.
5. Correct the value by going back to the field.

---

## 10. YAML Comment Preservation (ruamel.yaml)

1. Manually edit a comment in `global.yaml`:

```bash
# Add a comment before a field, save
```

2. Open the TUI, edit any field in the same section, save.

**Verify:**

```bash
bat ~/.inaki/config/global.yaml
```

- [ ] The manually inserted comment is still present.
- [ ] Other comments from the original file were not removed.

---

## 11. `inaki setup secret-key` — legacy Fernet wizard

```bash
inaki setup secret-key
```

- [ ] The interactive wizard opens.
- [ ] No traceback or import error.

---

## 12. `inaki setup webui` — placeholder

```bash
inaki setup webui
```

- [ ] Prints: `Coming soon — use \`inaki setup tui\` for now.`
- [ ] Exits with code 0 (`echo $?` → `0`).

---

## 13. Performance on Pi 5 (4 GB RAM)

- [ ] The TUI opens in <= 3 seconds from command to first render.
- [ ] Navigating between `GlobalPage` sections responds without visible lag.
- [ ] Saving a field does not freeze the UI for more than 1 second.

If notable sluggishness occurs, document:
- which operation was slow
- approximately how long it took
- whether it improves using `PYTHONOPTIMIZE=1`

---

## 14. Post-smoke Cleanup

```bash
rm -f ~/.inaki/config/agents/smoke-test.yaml
# Restore global.yaml if it was modified
# To see the welcome modal again:
rm -f ~/.inaki/setup_welcome_seen
```

---

## Acceptance Criteria

All items marked checked. If any fails, create an issue with:
- terminal output (or TUI screenshot)
- Python version (`python --version`)
- Textual version (`pip show textual`)
- Pi model and RAM amount
