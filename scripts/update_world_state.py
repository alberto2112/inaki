#!/usr/bin/env python3
"""
Genera WORLD_STATE.md consultando directamente las bases de datos SQLite.
Sin llamadas LLM, sin despertar al agente.
"""

import sqlite3
from datetime import datetime
from pathlib import Path

SCHEDULER_DB = Path.home() / ".inaki" / "scheduler.db"
MEMORIES_DB = Path.home() / ".inaki" / "mem" / "memories.db"
OUTPUT_FILE = Path.home() / ".inaki" / "users" / "telegram" / "WORLD_STATE.md"

CHAT_ID = "4879536"


def get_scheduled_tasks():
    """Consulta tareas activas del scheduler"""
    if not SCHEDULER_DB.exists():
        return []

    conn = sqlite3.connect(SCHEDULER_DB)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, name, schedule, next_run, status
        FROM scheduled_tasks
        WHERE enabled = 1 AND status = 'pending'
        ORDER BY next_run
    """)
    tasks = []
    for row in cursor.fetchall():
        task_id, name, schedule, next_run, status = row
        next_run_str = datetime.fromtimestamp(next_run).strftime("%Y-%m-%d %H:%M") if next_run else "N/A"
        tasks.append(f"- [{task_id}] {name} — próxima ejecución: {next_run_str}")
    conn.close()
    return tasks


def get_active_projects():
    """Busca proyectos activos en memoria (solo del usuario)"""
    if not MEMORIES_DB.exists():
        return []

    conn = sqlite3.connect(MEMORIES_DB)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT content, tags
        FROM memories
        WHERE deleted = 0
          AND chat_id = ?
          AND (tags LIKE '%proyecto%' OR tags LIKE '%trabajo%')
          AND created_at > date('now', '-30 days')
        ORDER BY created_at DESC
        LIMIT 10
    """, (CHAT_ID,))
    projects = []
    for row in cursor.fetchall():
        content, tags = row
        projects.append(f"- {content}")
    conn.close()
    return projects


def get_recent_alerts():
    """Busca alertas recientes en memoria (solo del usuario)"""
    if not MEMORIES_DB.exists():
        return []

    conn = sqlite3.connect(MEMORIES_DB)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT content
        FROM memories
        WHERE deleted = 0
          AND chat_id = ?
          AND tags LIKE '%alerta%'
          AND created_at > date('now', '-7 days')
        ORDER BY created_at DESC
        LIMIT 5
    """, (CHAT_ID,))
    alerts = [row[0] for row in cursor.fetchall()]
    conn.close()
    return alerts


def generate_world_state():
    """Genera el archivo WORLD_STATE.md"""
    now = datetime.now()

    tasks = get_scheduled_tasks()
    projects = get_active_projects()
    alerts = get_recent_alerts()

    content = f"""# Estado del mundo — {now.strftime("%Y-%m-%d %H:%M")}

## Tareas programadas activas
{chr(10).join(tasks) if tasks else "- (ninguna)"}

## Próximos eventos (7 días)
- (consulta Exchange no disponible)

## Proyectos activos (de memoria)
{chr(10).join(projects) if projects else "- (ninguno detectado)"}

## Alertas pendientes
{chr(10).join(alerts) if alerts else "- (ninguna)"}
"""

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(content, encoding='utf-8')
    print(f"✓ WORLD_STATE.md actualizado: {now.strftime('%H:%M')}")


if __name__ == "__main__":
    generate_world_state()
