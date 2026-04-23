# Concurrencia en grupos multi-agente — problema y solución pendiente

## El problema: timing del broadcast context

Cuando dos instancias de Iñaki (`A` y `B`) están en el mismo grupo Telegram con
`behavior: autonomous`, ambas reciben el mismo mensaje del usuario casi al mismo tiempo.

```
Usuario → mensaje → [Telegram]
                        ├── → Bot A (empieza a procesar)
                        └── → Bot B (empieza a procesar)
```

Cada bot lee el `BroadcastBuffer` al inicio de `_run_pipeline` para construir el
`extra_sections` del system prompt. En ese momento el buffer del otro bot aún está vacío
(o contiene contexto de turnos anteriores, no del actual). Los dos bots producen sus
respuestas sin verse mutuamente.

Después de responder, cada uno emite vía TCP al otro:

```
Bot A responde → emit(BroadcastMessage) → Bot B buffer
Bot B responde → emit(BroadcastMessage) → Bot A buffer
```

Resultado: el primer mensaje simultáneo carece de contexto cruzado. Los mensajes
siguientes sí lo tienen, porque el buffer ya está populado del turno anterior.

## Por qué no hay bucle infinito

Tres mecanismos lo evitan:

1. **Anti-loop en el TCP adapter**: `TcpBroadcastAdapter` descarta cualquier mensaje cuyo
   `agent_id` coincida con el propio. Un bot nunca ve su propio broadcast.
2. **Mensajes de bot ignorados por Telegram**: la Bot API no entrega a un bot los mensajes
   de OTRO bot (limitación de plataforma). El broadcast lateral existe precisamente para
   compensar esto — pero también significa que los bots no se "responden" entre sí desde
   el punto de vista de Telegram.
3. **`[SKIP]` en modo autonomous**: cuando el LLM considera que no tiene nada útil que
   aportar, responde exactamente `[SKIP]`. El pipeline detecta el marcador y no envía
   nada ni emite broadcast.

## Estado actual: opción C (aceptar el primer turno sin contexto cruzado)

Se eligió no hacer nada. Razonamiento:

- El buffer siempre tiene el contexto del turno **anterior** del otro bot — solo el primer
  mensaje simultáneo queda "ciego".
- En una conversación fluida, el impacto es mínimo: los bots se ven a partir del segundo
  intercambio.
- No agrega latencia perceptible al usuario.
- Cero complejidad extra.

## Opción B: semáforo por `chat_id` (NO implementado)

### Idea

Un `asyncio.Semaphore(1)` por `chat_id` de grupo. Mientras un bot procesa un mensaje
del grupo, el siguiente mensaje del mismo grupo espera a que termine. Esto serializa el
procesamiento por chat.

### Cómo funcionaría

En `_handle_group_message`, antes de entrar al pipeline:

```python
# En __init__:
self._group_semaphores: dict[int, asyncio.Semaphore] = {}

# En _handle_group_message:
sem = self._group_semaphores.setdefault(chat_id, asyncio.Semaphore(1))
async with sem:
    await self._run_pipeline(update, contenido_grupo, chat_type=chat_type)
```

### Por qué esto mejora el timing

Si bot A adquiere el semáforo primero:

```
Bot A adquiere semáforo → procesa → responde → emite broadcast → libera semáforo
Bot B esperaba          → adquiere semáforo → buffer YA tiene la respuesta de A → procesa con contexto
```

Bot B ahora VE la respuesta de A antes de generar la suya.

### Tradeoffs

| Aspecto | Impacto |
|---------|---------|
| Latencia percibida | El segundo bot espera que el primero termine (~2-5s de inferencia LLM). El usuario ve las dos respuestas con un pequeño delay entre ellas en lugar de casi simultáneas. |
| Complejidad | ~10 líneas. Dict de semáforos crece con el número de grupos activos — no es un problema práctico. |
| Timeout | Si el pipeline del primer bot falla sin liberar el semáforo, el segundo se bloquea. Mitigación: `asyncio.wait_for` con timeout configurable (`broadcast_pipeline_timeout_seconds: 30`). |
| Fairness | PTB encola updates internamente — si llegan muchos mensajes seguidos al mismo grupo, se procesan de a uno. En grupos activos puede introducir backpressure visible. |
| Efecto en `[SKIP]` | Si bot A responde `[SKIP]`, libera el semáforo sin emitir broadcast. Bot B adquiere el semáforo pero el buffer de A sigue vacío para ese turno. Comportamiento correcto — no hay nada que ver. |

### Implementación sugerida cuando se decida

Archivo a tocar: `adapters/inbound/telegram/bot.py`

1. Agregar `self._group_semaphores: dict[int, asyncio.Semaphore] = {}` en `__init__`.
2. En `_handle_group_message`, después de validar `allowed_chat_ids` y antes de `await self._run_pipeline(...)`:

```python
_sem = self._group_semaphores.setdefault(chat_id, asyncio.Semaphore(1))
try:
    await asyncio.wait_for(_sem.acquire(), timeout=30.0)
except asyncio.TimeoutError:
    logger.warning(
        "Timeout esperando semáforo de grupo (chat_id=%s, agent=%s) — procesando sin serializar",
        chat_id,
        self._agent_cfg.id,
    )
else:
    try:
        await self._run_pipeline(update, contenido_grupo, chat_type=chat_type, extra_sections=extra_sections)
        return
    finally:
        _sem.release()
# Fallback si timeout: procesar sin semáforo
await self._run_pipeline(update, contenido_grupo, chat_type=chat_type, extra_sections=extra_sections)
```

3. Considerar limpiar entradas viejas del dict con un TTL si hay muchos grupos.

## Decisión

**Fecha**: 2026-04-23  
**Elegida**: Opción C (sin semáforo).  
**Revisitar si**: el contexto cruzado faltante en el primer turno se vuelve molesto en la
práctica, o si se agregan más de dos bots al mismo grupo (la ventana de colisión aumenta).
