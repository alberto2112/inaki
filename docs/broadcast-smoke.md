# Smoke test manual — broadcast multi-agente

Plan de prueba end-to-end para verificar el canal de broadcast entre dos instancias
de Iñaki en la misma LAN. Se ejecuta manualmente antes de dar por buena una puesta
en producción nueva o tras cualquier cambio en `adapters/broadcast/` o el handler
Telegram.

---

## Setup requerido

- **Dos Raspberry Pi** en la misma red local (o dos procesos en la misma máquina
  con IPs/puertos distintos).
- **Dos bots de Telegram distintos** con sus tokens (obtenidos de @BotFather).
  Llamémoslos `inaki_a_bot` y `inaki_b_bot`.
- **Un grupo de Telegram** con ambos bots agregados como administradores.
- Ambos Pi con NTP activo (`timedatectl status` muestra `NTP service: active`).

---

## Pasos de clean-rebuild (hacer SIEMPRE antes del primer test)

1. Detener el daemon en ambos Pi:
   ```bash
   sudo systemctl stop inaki
   ```

2. Eliminar las DBs de historia y memoria en ambos Pi:
   ```bash
   rm ~/.inaki/data/history.db ~/.inaki/data/inaki.db
   ```
   Esto fuerza la creación del schema nuevo con columnas `channel` y `chat_id`.

3. Configurar `~/.inaki/config/agents/<id>.yaml` en el **Pi servidor** (inaki_a):
   ```yaml
   channels:
     telegram:
       allowed_user_ids: [TU_USER_ID]
       allowed_chat_ids: []          # se llenará después del Escenario D
       broadcast:
         port: 1234
         auth: "shared-secret-entre-agentes"
         bot_username: "inaki_a_bot"
         behavior: mention
         rate_limiter: 5
   ```

4. Configurar `~/.inaki/config/agents/<id>.yaml` en el **Pi cliente** (inaki_b):
   ```yaml
   channels:
     telegram:
       allowed_user_ids: [TU_USER_ID]
       allowed_chat_ids: []
       broadcast:
         remote:
           host: "192.168.1.10:1234"           # IP del Pi servidor
           auth: "shared-secret-entre-agentes"
         bot_username: "inaki_b_bot"
         behavior: mention
         rate_limiter: 5
   ```
   El secreto `auth` **debe ser idéntico** en ambos lados.

5. Iniciar el daemon en ambos Pi (primero el servidor, luego el cliente):
   ```bash
   sudo systemctl start inaki
   ```

6. Verificar que no haya errores de arranque:
   ```bash
   journalctl -u inaki -n 50 --no-pager
   ```
   Esperado: ningún `ERROR` ni `CRITICAL`. El cliente loguea algo como
   `broadcast.client.connected` cuando establece la conexión TCP.

---

## Escenario A — modo `listen`

**Objetivo:** ningún bot responde. Ambos ingresan los mensajes al buffer de broadcast.

**Preparación:** poner `behavior: listen` en ambos lados. Reiniciar ambos daemons.

**Pasos:**

1. Desde tu cuenta (en `allowed_user_ids`), escribí un mensaje en el grupo.
2. **Esperado:** ningún bot responde en Telegram.
3. En los logs del Pi_A:
   ```
   journalctl -u inaki -f
   ```
   **Esperado:** entrada de log mostrando que el mensaje fue recibido e ingresado
   al buffer (evento `broadcast.buffer.append` o equivalente). Sin `reply_text`.
4. En los logs del Pi_B:
   **Esperado:** ídem — el mensaje llegó vía TCP y fue procesado.
5. Escribí un segundo mensaje en el grupo.
6. **Esperado:** sigue sin respuesta en Telegram. El buffer crece.

---

## Escenario B — modo `mention`

**Objetivo:** un bot responde solo cuando se le menciona. El otro absorbe la respuesta
en su buffer.

**Preparación:** `behavior: mention` en ambos lados (es el default). Reiniciar daemons.

**Pasos:**

1. Escribí un mensaje sin menciones en el grupo.
   **Esperado:** ningún bot responde.
2. Escribí `@inaki_a_bot hola` en el grupo.
   **Esperado:** solo `inaki_a_bot` responde. `inaki_b_bot` no responde.
3. En los logs del Pi_B verificar que la respuesta de A llegó por broadcast:
   **Esperado:** entrada de log con `broadcast.buffer.append` con `agent_id=inaki_a`.
4. Escribí `@inaki_b_bot hola` en el grupo.
   **Esperado:** solo `inaki_b_bot` responde. La respuesta de B aparece en el buffer de A.
5. Verificar en logs de Pi_A que recibió la respuesta de B.

---

## Escenario C — modo `autonomous` con rate limiter

**Objetivo:** los bots responden por su cuenta. El rate limiter corta tras N respuestas
en 30 segundos.

**Preparación:** `behavior: autonomous` y `rate_limiter: 3` en ambos lados. Reiniciar.

**Pasos:**

1. Escribí un mensaje en el grupo.
   **Esperado:** uno o ambos bots pueden responder (el LLM decide). Si el LLM responde
   `[SKIP]` internamente, no aparece nada en Telegram.
2. Escribí 4 mensajes seguidos en menos de 30 segundos.
   **Esperado:** después de 3 respuestas de algún bot, los mensajes siguientes se
   silencian. En los logs del Pi correspondiente aparece un evento de límite alcanzado
   (p. ej. `rate_limiter.breach`).
3. Esperá 30 segundos y escribí otro mensaje.
   **Esperado:** el bot vuelve a estar habilitado para responder.

---

## Escenario D — bootstrap de `chat_id` con `/chatid`

**Objetivo:** obtener el `chat_id` del grupo para rellenar `allowed_chat_ids`.

**Preparación:** dejar `allowed_chat_ids: []` (lista vacía) en la config de ambos
agentes. Reiniciar daemons.

**Pasos:**

1. Desde tu cuenta, enviá `/chatid` en el grupo.
   **Esperado:** el bot responde con el `chat_id` numérico del grupo
   (un entero negativo grande, p. ej. `-1001234567890`).
2. Verificar que el comando funciona aunque `allowed_chat_ids` esté vacío — eso
   confirma que `/chatid` bypasea la validación de grupos.
3. Intentar escribir un mensaje normal (sin menciones) en el grupo.
   **Esperado (si el grupo no está en `allowed_chat_ids`):** el bot ignora el mensaje.
   En logs: `telegram.mensaje.grupo_no_autorizado` o similar.
4. Agregar el `chat_id` a `allowed_chat_ids` en la config de ambos lados y reiniciar.
5. Repetir los escenarios anteriores — ahora los mensajes del grupo pasan el filtro.

---

## Escenario E — HMAC incorrecto (auth mismatch)

**Objetivo:** verificar que un mensaje con auth incorrecto se descarta.

**Preparación:** cambiar el `auth` del Pi_B a un valor distinto (sin reiniciar Pi_A).
Reiniciar solo el daemon de Pi_B.

**Pasos:**

1. Enviar un mensaje en el grupo para que Pi_B lo emita por broadcast con auth incorrecto.
2. En los logs del Pi_A verificar:
   **Esperado:** entrada de log con evento `broadcast.message.dropped.hmac_mismatch`.
   Pi_A no procesa el mensaje.
3. Restaurar el `auth` correcto en Pi_B y reiniciar. La comunicación vuelve a funcionar.

---

## Escenario F — NTP drift (relojes desincronizados)

**Objetivo:** verificar que mensajes con timestamp fuera de la ventana de 60s se descartan.

**Preparación:** en el Pi_B, adelantar el reloj 2 minutos manualmente (requiere deshabilitar
NTP temporalmente):

```bash
sudo systemctl stop systemd-timesyncd
sudo date -s "+2 minutes"
```

**Pasos:**

1. Enviar un mensaje en el grupo.
2. Pi_B emite el mensaje por broadcast con un timestamp 2 minutos en el futuro.
3. En los logs del Pi_A verificar:
   **Esperado:** entrada de log con evento `broadcast.message.dropped.stale_timestamp`.
   Pi_A descarta el mensaje silenciosamente (no aparece en el buffer ni en el contexto del LLM).
4. Restaurar el reloj en Pi_B:
   ```bash
   sudo systemctl start systemd-timesyncd
   ```
   Esperar unos segundos a que NTP resincronice. El broadcast vuelve a funcionar.

---

## Escenario E: Eventos tipificados — `user_input_voice` y `user_input_photo`

**Setup adicional**: en Pi_A activar `broadcast.emit.user_input_voice: true` y
`broadcast.emit.user_input_photo: true` en el YAML. En Pi_B dejarlos en `false` (default).
Reiniciar ambos daemons. Pi_A debe tener `voice_enabled: true` y un `process_photo` wired
(secciones de transcripción y `photos:` en config global).

### E1 — Audio: Pi_A transcribe y comparte la transcripción

**Objetivo:** que Pi_B reciba la transcripción del audio que solo Pi_A procesó.

1. Desde tu cuenta humana, mandá un mensaje de voz al grupo.
2. Pi_A reacciona 🔊, transcribe el audio y dispara su pipeline normal.
3. **Esperado en logs de Pi_A**: entrada con evento `broadcast.message.received` o
   equivalente que liste `event_type=user_input_voice` con la transcripción en `content`
   y el username humano en `sender`.
4. **Esperado en buffer de Pi_B**: render del contexto incluye `[HH:MM:SS] {sender}
   (audio): {transcripción}` cuando Pi_B sea mencionado en un turno posterior.
5. Pi_B no transcribe el audio raw (no tiene la capacidad activa) pero ve el resultado.

### E2 — Foto: Pi_A describe y comparte la descripción

**Objetivo:** que Pi_B reciba la descripción de escena de una foto que solo Pi_A procesó.

1. Mandá una foto al grupo.
2. Pi_A reacciona 👁, corre `process_photo` y dispara el pipeline normal.
3. **Esperado en logs de Pi_A**: `event_type=user_input_photo` con la descripción en
   `content`. El evento se emite **antes** del `assistant_response`.
4. **Esperado en buffer de Pi_B**: línea `[HH:MM:SS] {sender} (foto): {descripción}`.
5. **Edge case modo `!`**: si la foto va con caption `!transcribí esto`, Pi_A escribe
   directo al chat sin pasar por LLM y emite **solo** `user_input_photo` (no
   `assistant_response`). Pi_B ve el evento de foto pero no una respuesta de agente.

### E3 — Versiones desincronizadas (wire format breaking change)

**Setup**: actualizar Pi_A pero **no** Pi_B (downgrade pre-cambio).

1. Pi_A emite con el wire format nuevo (campos `event_type`, `sender`, `content`).
2. Pi_B parsea con código viejo: el HMAC canonical es distinto → mismatch → mensaje
   descartado silenciosamente.
3. **Esperado en Pi_B**: log `broadcast.message.dropped.hmac_mismatch`.

**Mitigación**: este change es upgrade-en-bloque. Detener daemon en TODOS los Pis del
LAN, actualizar código simultáneo, restart. Sin DB schema changes — solo wire format.
