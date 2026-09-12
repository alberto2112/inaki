"""``GroupRateLimit``: intervenciones consecutivas + cooldown, aislado del bot y de ``/ratelimit``.

El contrato que prueban estos tests: un agente puede responder ``max_count``
veces SEGUIDAS en un chat; la última dispara un cooldown de ``window_seconds``
contado desde esa intervención; un humano lo re-arma al instante.
"""

from __future__ import annotations

from inaki.channels.telegram.rate_limit import GroupRateLimit


class _Reloj:
    """Fuente de tiempo inyectable: los cooldowns se prueban sin dormir."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def avanzar(self, segundos: float) -> None:
        self.t += segundos


def _policy(
    *, enabled: bool = True, max_count: int = 2, window: int = 60
) -> tuple[GroupRateLimit, _Reloj]:
    reloj = _Reloj()
    return (
        GroupRateLimit(
            enabled=enabled,
            agent_id="dev",
            max_count=max_count,
            window_seconds=window,
            _now=reloj,
        ),
        reloj,
    )


def test_deshabilitada_todo_es_no_op() -> None:
    p, _ = _policy(enabled=False, max_count=1)
    assert not p.enabled
    for _ in range(5):
        p.record_response("-1")
        assert p.cooldown("-1") is None, "sin behavior=autonomous no se limita nada"


def test_responde_hasta_max_count_seguidas_y_entra_en_cooldown() -> None:
    p, _ = _policy(max_count=2)
    assert p.cooldown("-1") is None
    p.record_response("-1")
    assert p.cooldown("-1") is None, "la primera no agota el presupuesto"
    p.record_response("-1")
    enfriando = p.cooldown("-1")
    assert enfriando is not None
    assert enfriando.consecutive == 2
    assert enfriando.retry_in == 60.0
    assert p.cooldown("-2") is None, "el presupuesto es por chat"


def test_el_cooldown_se_cuenta_desde_la_ultima_intervencion() -> None:
    """La diferencia con la ventana fija: el reloj lo arranca el agente al hablar."""
    p, reloj = _policy(max_count=2, window=200)
    p.record_response("-1")
    reloj.avanzar(180.0)  # el otro bot marcó el ritmo durante 3 minutos
    p.record_response("-1")

    enfriando = p.cooldown("-1")
    assert enfriando is not None and enfriando.retry_in == 200.0, (
        "el cooldown dura la ventana entera desde la 2da respuesta, "
        "no lo que quedaba de una ventana abierta en el primer mensaje"
    )
    reloj.avanzar(199.9)
    assert p.cooldown("-1") is not None
    reloj.avanzar(0.2)
    assert p.cooldown("-1") is None, "vencido, se re-arma solo"


def test_humano_rearma_al_instante() -> None:
    p, _ = _policy(max_count=1)
    p.record_response("-1")
    assert p.cooldown("-1") is not None
    p.reset("-1")
    assert p.cooldown("-1") is None
    p.record_response("-1")
    assert p.cooldown("-1") is not None, "el contador arrancó de cero, no de uno"


def test_un_humano_en_un_chat_no_rearma_otro() -> None:
    p, _ = _policy(max_count=1)
    p.record_response("-1")
    p.record_response("-2")
    p.reset("-1")
    assert p.cooldown("-1") is None
    assert p.cooldown("-2") is not None


def test_solo_lo_emitido_cuenta() -> None:
    """``cooldown()`` es un gate sin efectos: preguntar no gasta presupuesto."""
    p, _ = _policy(max_count=2)
    for _ in range(10):
        assert p.cooldown("-1") is None
    p.record_response("-1")
    assert p.cooldown("-1") is None


def test_loop_bot_a_bot_termina_sin_humano() -> None:
    """Regresión del incidente: dos autónomos turnándose no se cortaban nunca.

    Con la ventana fija, el ciclo bot-a-bot (~70s) giraba la ventana antes de
    agotarla y el contador volvía a 1 solo. Con intervenciones consecutivas, el
    agente se calla y NO vuelve hasta que pase el cooldown entero.
    """
    p, reloj = _policy(max_count=2, window=300)
    respuestas = 0
    for _ in range(40):  # 40 turnos del otro bot, ~47 minutos de reloj
        if p.cooldown("-1") is None:
            p.record_response("-1")
            respuestas += 1
        reloj.avanzar(70.0)
    assert respuestas == 14, (
        "de 40 turnos del otro bot solo 14 se contestan: 2 seguidas y a enfriar. "
        "Sin humano el caudal queda acotado por la config, no por el ritmo que "
        "impone el otro bot (con la ventana fija se contestaban los 40)"
    )


def test_set_y_restore_defaults_mutan_count_y_cooldown() -> None:
    p, _ = _policy(max_count=5, window=60)

    p.set(7, 300)
    assert p.max_count == 7 and p.window_seconds == 300
    p.set(3)
    assert p.max_count == 3 and p.window_seconds == 300, "sin window no la toca"

    p.restore_defaults()
    assert p.max_count == 5 and p.default_max_count == 5
    assert p.window_seconds == 60 and p.default_window_seconds == 60
