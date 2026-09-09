"""Scheduler: tareas programadas (tier harness-global).

``domain/`` (entidades, cron, parser de fechas), ``ports/`` (repo, use case,
despacho), ``use_cases/schedule_task`` (CRUD con guardrails), ``service``
(el loop y ``run_task_now``), ``reconciler`` (builtins contra la config),
``adapters/`` (SQLite, dispatch, builtins) y ``tools/`` (la tool del LLM).
Ningún port lo consume el kernel: el turno no sabe que existe un scheduler.
"""
