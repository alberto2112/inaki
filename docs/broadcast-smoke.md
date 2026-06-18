# Manual Smoke Test — Multi-Agent Broadcast

End-to-end test plan to verify the broadcast channel between two Inaki instances
on the same LAN. Run manually before greenlighting a new production deployment or
after any change in `adapters/broadcast/` or the Telegram handler.

---

## Required Setup

- **Two Raspberry Pis** on the same local network (or two processes on the same machine
  with different IPs/ports).
- **Two separate Telegram bots** with their tokens (obtained from @BotFather).
  Let's call them `inaki_a_bot` and `inaki_b_bot`.
- **A Telegram group** with both bots added as administrators.
- Both Pis with active NTP (`timedatectl status` shows `NTP service: active`).

---

## Clean-Rebuild Steps (ALWAYS do this before the first test)

1. Stop the daemon on both Pis:
   ```bash
   sudo systemctl stop inaki
   ```

2. Delete the history and memory DBs on both Pis:
   ```bash
   rm ~/.inaki/data/history.db ~/.inaki/data/inaki.db
   ```
   This forces creation of the new schema with `channel` and `chat_id` columns.

3. Configure `~/.inaki/config/agents/<id>.yaml` on the **server Pi** (inaki_a):
   ```yaml
   channels:
     telegram:
       allowed_user_ids: [YOUR_USER_ID]
       allowed_chat_ids: []          # will be filled after Scenario D
       groups:
         behavior: mention
         bot_username: "inaki_a_bot"
         rate_limiter: 5
       broadcast:
         port: 1234
         auth: "shared-secret-between-agents"
   ```

4. Configure `~/.inaki/config/agents/<id>.yaml` on the **client Pi** (inaki_b):
   ```yaml
   channels:
     telegram:
       allowed_user_ids: [YOUR_USER_ID]
       allowed_chat_ids: []
       groups:
         behavior: mention
         bot_username: "inaki_b_bot"
         rate_limiter: 5
       broadcast:
         remote:
           host: "192.168.1.10:1234"           # Server Pi IP
           auth: "shared-secret-between-agents"
   ```
   The `auth` secret **must be identical** on both sides.

5. Start the daemon on both Pis (server first, then client):
   ```bash
   sudo systemctl start inaki
   ```

6. Verify no startup errors:
   ```bash
   journalctl -u inaki -n 50 --no-pager
   ```
   Expected: no `ERROR` or `CRITICAL`. The client logs something like
   `broadcast.client.connected` when the TCP connection is established.

---

## Scenario A — `listen` mode

**Objective:** no bot responds. Both ingest messages into the broadcast buffer.

**Preparation:** set `behavior: listen` on both sides. Restart both daemons.

**Steps:**

1. From your account (in `allowed_user_ids`), send a message in the group.
2. **Expected:** no bot responds in Telegram.
3. In the Pi_A logs:
   ```
   journalctl -u inaki -f
   ```
   **Expected:** log entry showing the message was received and ingested
   into the buffer (`broadcast.buffer.append` event or equivalent). No `reply_text`.
4. In the Pi_B logs:
   **Expected:** same — the message arrived via TCP and was processed.
5. Send a second message in the group.
6. **Expected:** still no response in Telegram. The buffer grows.

---

## Scenario B — `mention` mode

**Objective:** a bot responds only when mentioned. The other absorbs the response
into its buffer.

**Preparation:** `behavior: mention` on both sides (this is the default). Restart daemons.

**Steps:**

1. Send a message without mentions in the group.
   **Expected:** no bot responds.
2. Send `@inaki_a_bot hola` in the group.
   **Expected:** only `inaki_a_bot` responds. `inaki_b_bot` does not respond.
3. In the Pi_B logs verify that A's response arrived via broadcast:
   **Expected:** log entry with `broadcast.buffer.append` with `agent_id=inaki_a`.
4. Send `@inaki_b_bot hola` in the group.
   **Expected:** only `inaki_b_bot` responds. B's response appears in A's buffer.
5. Verify in Pi_A logs that it received B's response.

---

## Scenario C — `autonomous` mode with rate limiter

**Objective:** bots respond on their own. The rate limiter cuts off after N responses
within 30 seconds.

**Preparation:** `behavior: autonomous` and `rate_limiter: 3` on both sides. Restart.

**Steps:**

1. Send a message in the group.
   **Expected:** one or both bots may respond (the LLM decides). If the LLM responds
   `[SKIP]` internally, nothing appears in Telegram.
2. Send 4 messages in a row within 30 seconds.
   **Expected:** after 3 responses from a bot, subsequent messages are
   silenced. In the logs of the corresponding Pi a rate limit reached event appears
   (e.g., `rate_limiter.breach`).
3. Wait 30 seconds and send another message.
   **Expected:** the bot is enabled to respond again.

---

## Scenario D — `chat_id` bootstrap with `/chatid`

**Objective:** obtain the group's `chat_id` to populate `allowed_chat_ids`.

**Preparation:** leave `allowed_chat_ids: []` (empty list) in both agents' config.
Restart daemons.

**Steps:**

1. From your account, send `/chatid` in the group.
   **Expected:** the bot replies with the group's numeric `chat_id`
   (a large negative integer, e.g., `-1001234567890`).
2. Verify the command works even though `allowed_chat_ids` is empty — this
   confirms that `/chatid` bypasses group validation.
3. Try sending a normal message (without mentions) in the group.
   **Expected (if the group is not in `allowed_chat_ids`):** the bot ignores the message.
   In logs: `telegram.mensaje.grupo_no_autorizado` or similar.
4. Add the `chat_id` to `allowed_chat_ids` in both sides' config and restart.
5. Repeat the previous scenarios — now group messages pass the filter.

---

## Scenario E — Incorrect HMAC (auth mismatch)

**Objective:** verify that a message with incorrect auth is discarded.

**Preparation:** change Pi_B's `auth` to a different value (without restarting Pi_A).
Restart only Pi_B's daemon.

**Steps:**

1. Send a message in the group so Pi_B broadcasts it with incorrect auth.
2. In Pi_A's logs verify:
   **Expected:** log entry with event `broadcast.message.dropped.hmac_mismatch`.
   Pi_A does not process the message.
3. Restore the correct `auth` on Pi_B and restart. Communication works again.

---

## Scenario F — NTP drift (desynchronized clocks)

**Objective:** verify that messages with timestamps outside the 60s window are discarded.

**Preparation:** on Pi_B, advance the clock 2 minutes manually (requires temporarily
disabling NTP):

```bash
sudo systemctl stop systemd-timesyncd
sudo date -s "+2 minutes"
```

**Steps:**

1. Send a message in the group.
2. Pi_B broadcasts the message with a timestamp 2 minutes in the future.
3. In Pi_A's logs verify:
   **Expected:** log entry with event `broadcast.message.dropped.stale_timestamp`.
   Pi_A silently discards the message (it does not appear in the buffer or in the LLM context).
4. Restore the clock on Pi_B:
   ```bash
   sudo systemctl start systemd-timesyncd
   ```
   Wait a few seconds for NTP to resync. Broadcast works again.

---

## Scenario E: Typed Events — `user_input_voice` and `user_input_photo`

**Additional setup**: on Pi_A enable `broadcast.emit.user_input_voice: true` and
`broadcast.emit.user_input_photo: true` in the YAML. On Pi_B leave them at `false` (default).
Restart both daemons. Pi_A must have `voice_enabled: true` and a `process_photo` wired
(transcription and `photos:` sections in global config).

### E1 — Audio: Pi_A transcribes and shares the transcription

**Objective:** have Pi_B receive the audio transcription that only Pi_A processed.

1. From your human account, send a voice message to the group.
2. Pi_A reacts with 🔊, transcribes the audio and triggers its normal pipeline.
3. **Expected in Pi_A logs**: entry with event `broadcast.message.received` or
   equivalent listing `event_type=user_input_voice` with the transcription in `content`
   and the human username in `sender`.
4. **Expected in Pi_B's buffer**: context render includes `[HH:MM:SS] {sender}
   (audio): {transcription}` when Pi_B is mentioned in a subsequent turn.
5. Pi_B does not transcribe the raw audio (it doesn't have the capability active) but sees the result.

### E2 — Photo: Pi_A describes and shares the description

**Objective:** have Pi_B receive the scene description of a photo that only Pi_A processed.

1. Send a photo to the group.
2. Pi_A reacts with 👁, runs `process_photo` and triggers the normal pipeline.
3. **Expected in Pi_A logs**: `event_type=user_input_photo` with the description in
   `content`. The event is emitted **before** the `assistant_response`.
4. **Expected in Pi_B's buffer**: line `[HH:MM:SS] {sender} (photo): {description}`.
5. **Edge case `!` mode**: if the photo has caption `!transcribí esto`, Pi_A writes
   directly to chat without going through the LLM and emits **only** `user_input_photo` (not
   `assistant_response`). Pi_B sees the photo event but not an agent response.

### E3 — Desynchronized Versions (wire format breaking change)

**Setup**: update Pi_A but **not** Pi_B (downgrade to pre-change).

1. Pi_A emits with the new wire format (fields `event_type`, `sender`, `content`).
2. Pi_B parses with old code: the HMAC canonical is different → mismatch → message
   silently discarded.
3. **Expected on Pi_B**: log `broadcast.message.dropped.hmac_mismatch`.

**Mitigation**: this change is an all-at-once upgrade. Stop the daemon on ALL Pis in the
LAN, update code simultaneously, restart. No DB schema changes — wire format only.
