"""``GroupRateLimit``: la política mutable de rate limit, aislada del bot y de ``/ratelimit``."""

from __future__ import annotations

from inaki.channels.telegram.broadcast.rate_limiter import FixedWindowRateLimiter
from inaki.channels.telegram.rate_limit import GroupRateLimit


def _policy(limiter: FixedWindowRateLimiter | None, max_count: int = 2) -> GroupRateLimit:
    return GroupRateLimit(limiter, agent_id="dev", max_count=max_count, window_seconds=60)


def test_sin_limiter_todo_es_no_op() -> None:
    p = _policy(None)
    assert not p.enabled
    assert p.check("-1") is None and p.check("-1") is None and p.check("-1") is None
    p.reset("-1")
    p.set(9, 300)
    assert p.max_count == 9 and p.window_seconds == 60, "la ventana no existe sin limiter"


def test_check_consume_cupo_y_hace_breach_al_pasarse() -> None:
    p = _policy(FixedWindowRateLimiter(window_seconds=60.0), max_count=2)
    assert p.check("-1") is None
    assert p.check("-1") is None
    breach = p.check("-1")
    assert breach is not None and breach.counter == 3
    assert p.check("-2") is None, "el cupo es por chat"


def test_reset_arranca_la_ventana_de_cero() -> None:
    p = _policy(FixedWindowRateLimiter(window_seconds=60.0), max_count=1)
    assert p.check("-1") is None
    assert p.check("-1") is not None
    p.reset("-1")
    assert p.check("-1") is None


def test_set_y_restore_defaults_mutan_count_y_ventana() -> None:
    limiter = FixedWindowRateLimiter(window_seconds=60.0)
    p = _policy(limiter, max_count=5)

    p.set(7, 300)
    assert p.max_count == 7 and limiter.window_seconds == 300.0
    p.set(3)
    assert p.max_count == 3 and limiter.window_seconds == 300.0, "sin window no la toca"

    p.restore_defaults()
    assert p.max_count == 5 and p.default_max_count == 5
    assert limiter.window_seconds == 60.0 and p.window_seconds == 60
