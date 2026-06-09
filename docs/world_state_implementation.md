# Sistema de Estado del Mundo

## Descripción general

El sistema de "estado del mundo" mantiene un resumen vivo y actualizado del contexto operativo de Alberto, incluyendo:

- Tareas programadas activas
- Próximos eventos del calendario (Exchange)
- Proyectos activos (desde memoria)
- Alertas pendientes
- Hilos abiertos

Este archivo se inyecta en el prompt del agente junto con el archivo de consolidate, proporcionando contexto inmediato sobre "qué está pasando ahora mismo".

## Arquitectura actual

### Componentes

1. **Tool `get_world_state()`**
   - Ubicación: `/home/production/.inaki/ext/get_world_state/`
   - Propósito: Genera el esqueleto del archivo WORLD_STATE.md
   - Uso: El agente la llama como punto de partida y luego rellena con datos reales

2. **Script Python `update_world_state.py`**
   - Ubicación: `/home/production/bin/update_world_state.py`
   - Propósito: Actualiza el archivo WORLD_STATE.md sin llamadas LLM
   - Método: Consultas SQL directas a las bases de datos del sistema

3. **Scheduler recurrente (tarea 104)**
   - Cron: `0 5-22 * * *` (cada hora desde las 5:00 hasta las 22:00 CEST)
   - Tipo: `shell_exec`
   - Comando: `python3 /home/production/bin/update_world_state.py`

4. **Archivo WORLD_STATE.md**
   - Ubicación: `~/.inaki/users/telegram/WORLD_STATE.md`
   - Formato: Markdown estructurado con secciones
   - Inyección: Se carga en el prompt del agente automáticamente

### Flujo de ejecución

```
Scheduler (cada hora)
    ↓
Ejecuta update_world_state.py
    ↓
Script consulta:
  - SQLite scheduler (tareas activas)
  - SQLite memory (proyectos recientes)
    ↓
Genera WORLD_STATE.md
    ↓
Archivo disponible para el agente
```

## Limitaciones actuales

### 1. Sin acceso a Exchange
El script Python no puede consultar el calendario de Exchange porque las tools de Exchange solo están disponibles para el agente, no como librerías Python independientes.

**Impacto:** La sección "Próximos eventos" queda vacía o con datos incompletos.

### 2. Sin detección de hilos abiertos
Detectar "hilos abiertos" requiere análisis semántico del historial de conversaciones, lo cual necesita LLM.

**Impacto:** La sección "Hilos abiertos" no se actualiza automáticamente.

### 3. Proyectos activos limitados
El script solo muestra las últimas 10 memorias con tags "proyecto" o "trabajo", sin análisis de relevancia.

**Impacto:** Puede mostrar proyectos irrelevantes o faltar proyectos importantes.

## Soluciones evaluadas

### Opción A: Usar `agent_send` (descartada)
- **Ventaja:** Acceso completo a todas las tools (Exchange, memory, etc.)
- **Desventaja:** ~5-6 llamadas LLM por ejecución = ~85-100 llamadas LLM al día
- **Coste estimado:** ~2.500-3.000 llamadas LLM al mes
- **Veredicto:** Demasiado ineficiente

### Opción B: Script Python puro (implementada)
- **Ventaja:** 0 llamadas LLM, ejecución rápida y eficiente
- **Desventaja:** No puede acceder a Exchange ni hacer análisis semántico
- **Veredicto:** Eficiente pero incompleto

### Opción C: CLI mejorado (pendiente)
- **Propuesta:** Alberto va a mejorar el CLI de Iñaki para permitir ejecución puntual de tools desde scripts
- **Ventaja:** El script Python podría llamar a `exchange_calendar.search()` y `search_memory()` directamente
- **Desventaja:** Requiere desarrollo del CLI
- **Veredicto:** Solución óptima, pendiente de implementación

## Implementación futura

Cuando el CLI esté listo, el flujo será:

```
Scheduler (cada hora)
    ↓
Ejecuta update_world_state.py
    ↓
Script ejecuta:
  - inaki tool scheduler.list
  - inaki tool exchange_calendar.search --days 7
  - inaki tool search_memory --query "proyectos activos"
    ↓
Genera WORLD_STATE.md completo
    ↓
Archivo disponible para el agente
```

**Beneficios:**
- 1 llamada LLM por ejecución (solo para análisis semántico si es necesario)
- Acceso completo a todas las tools
- ~30 llamadas LLM al día (vs 85-100 de la opción A)

## Archivos relacionados

- `/home/production/bin/update_world_state.py` — Script de actualización
- `/home/production/.inaki/ext/get_world_state/` — Tool del agente
- `~/.inaki/users/telegram/WORLD_STATE.md` — Archivo generado
- `notes/ESTADO_DEL_MUNDO.md` — Notas del proyecto

## Historial de cambios

### 2026-06-09
- Implementación inicial con `agent_send` (ineficiente)
- Cambio a `shell_exec` con script Python (eficiente pero incompleto)
- Proyecto pausado hasta que el CLI esté listo
