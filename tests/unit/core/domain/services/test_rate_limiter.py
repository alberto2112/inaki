"""Tests unitarios para FixedWindowRateLimiter.

Cubre: incremento de contador, breach en límite >, reset al expirar ventana,
independencia de claves (agent_id, chat_id), y cálculo de retry_in.
Usa el parámetro injectable ``_now`` para tiempo determinista.

Semántica del breach: con ``limit=N``, exactamente ``N`` emisiones pasan; la
``(N+1)``-ésima es rechazada (counter > limit).
"""

from __future__ import annotations

from core.domain.services.rate_limiter import BreachSignal, FixedWindowRateLimiter


# ---------------------------------------------------------------------------
# Primeras llamadas — sin breach
# ---------------------------------------------------------------------------


def test_primer_increment_no_breach():
    """La primera llamada nunca produce breach (contador = 1)."""
    limiter = FixedWindowRateLimiter(_now=lambda: 1000.0)
    result = limiter.check_and_increment("agente_a", "chat_1", limit=3)
    assert result is None


def test_incrementos_dentro_del_limite_no_breach():
    """N llamadas dentro del límite no producen breach."""
    now = 1000.0
    limiter = FixedWindowRateLimiter(_now=lambda: now)
    for _ in range(4):
        result = limiter.check_and_increment("agente_a", "chat_1", limit=5)
        assert result is None


def test_contador_se_incrementa_dentro_de_ventana():
    """El counter sube en cada llamada dentro de la ventana activa."""
    now = 1000.0
    limiter = FixedWindowRateLimiter(_now=lambda: now)

    limiter.check_and_increment("a", "c", limit=100)
    limiter.check_and_increment("a", "c", limit=100)
    signal = limiter.check_and_increment("a", "c", limit=100)
    # counter = 3, limit = 100 → no breach
    assert signal is None


# ---------------------------------------------------------------------------
# Breach — umbral > limit (exactamente `limit` emisiones pasan)
# ---------------------------------------------------------------------------


def test_breach_solo_al_superar_limit():
    """El breach se activa cuando counter > limit. Con limit=N pasan N llamadas."""
    now = 1000.0
    limiter = FixedWindowRateLimiter(_now=lambda: now)
    limit = 3

    # Llamadas 1, 2, 3: no breach (counter == limit todavía pasa)
    assert limiter.check_and_increment("a", "c", limit) is None
    assert limiter.check_and_increment("a", "c", limit) is None
    assert limiter.check_and_increment("a", "c", limit) is None

    # Llamada 4: counter == limit + 1 → breach
    signal = limiter.check_and_increment("a", "c", limit)
    assert signal is not None
    assert isinstance(signal, BreachSignal)


def test_breach_contiene_agent_id_y_chat_id():
    """El BreachSignal incluye los campos agent_id y chat_id correctos."""
    now = 1000.0
    limiter = FixedWindowRateLimiter(_now=lambda: now)

    # Con limit=1 pasa la primera; la segunda es breach.
    limiter.check_and_increment("agente_z", "grupo_99", limit=1)
    signal = limiter.check_and_increment("agente_z", "grupo_99", limit=1)
    assert signal is not None
    assert signal.agent_id == "agente_z"
    assert signal.chat_id == "grupo_99"


def test_breach_counter_correcto():
    """El BreachSignal refleja el contador actual al momento del breach."""
    now = 1000.0
    limiter = FixedWindowRateLimiter(_now=lambda: now)
    limit = 2

    limiter.check_and_increment("a", "c", limit)  # counter=1
    limiter.check_and_increment("a", "c", limit)  # counter=2 = limit, pasa
    signal = limiter.check_and_increment("a", "c", limit)  # counter=3 > limit, breach

    assert signal is not None
    assert signal.counter == 3


# ---------------------------------------------------------------------------
# retry_in — cálculo correcto
# ---------------------------------------------------------------------------


def test_breach_retry_in_calculado():
    """retry_in = window_seconds - tiempo_en_ventana_actual."""
    tiempo_actual = 1000.0
    limiter = FixedWindowRateLimiter(window_seconds=30.0, _now=lambda: tiempo_actual)
    limit = 1

    # Abre ventana en t=1000
    limiter.check_and_increment("a", "c", limit)

    # Avanza 10s — quedan 20s para reset
    tiempo_actual = 1010.0
    signal = limiter.check_and_increment("a", "c", limit)

    assert signal is not None
    # retry_in debe ser aproximadamente 20.0 (window - elapsed = 30 - 10)
    assert abs(signal.retry_in - 20.0) < 0.1


def test_breach_retry_in_no_negativo():
    """retry_in nunca es negativo — mínimo 0.0."""
    tiempo_actual = 1000.0
    limiter = FixedWindowRateLimiter(window_seconds=30.0, _now=lambda: tiempo_actual)
    limit = 1

    # Abre ventana en t=1000
    limiter.check_and_increment("a", "c", limit)

    # Simula ventana ya expirada (condición de carrera improbable pero defensiva)
    tiempo_actual = 1031.0
    signal = limiter.check_and_increment("a", "c", limit)

    # Con ventana expirada debería reset → no breach, o si hay breach retry_in >= 0
    if signal is not None:
        assert signal.retry_in >= 0.0


# ---------------------------------------------------------------------------
# Reset al expirar ventana
# ---------------------------------------------------------------------------


def test_reset_de_ventana_reinicia_contador():
    """Cuando now - window_start >= 30s, la ventana se reinicia y el contador vuelve a 1."""
    tiempo_actual = 1000.0
    limiter = FixedWindowRateLimiter(window_seconds=30.0, _now=lambda: tiempo_actual)
    limit = 2

    # Llena la ventana hasta breach (con limit=2: pasan 2, breach en la 3ra)
    limiter.check_and_increment("a", "c", limit)  # counter=1
    limiter.check_and_increment("a", "c", limit)  # counter=2 = limit, pasa
    signal = limiter.check_and_increment("a", "c", limit)  # counter=3 > limit, breach
    assert signal is not None

    # Avanzar más de 30s → nueva ventana
    tiempo_actual = 1031.0
    signal_nuevo = limiter.check_and_increment("a", "c", limit)
    # Primera llamada en nueva ventana: counter=1, no breach
    assert signal_nuevo is None


def test_reset_exacto_en_limite_de_ventana():
    """El reset ocurre cuando now - window_start >= 30s (límite incluido).

    Nota de diseño: la primera llamada a check_and_increment inicializa la ventana
    y retorna None (nunca breach en primer intento). El breach ocurre cuando counter
    SUPERA el limit dentro de la ventana activa.
    """
    tiempo_actual = 1000.0
    limiter = FixedWindowRateLimiter(window_seconds=30.0, _now=lambda: tiempo_actual)
    limit = 2

    # Primera llamada: abre ventana, counter=1, no breach
    assert limiter.check_and_increment("a", "c", limit) is None

    # Segunda llamada: counter=2 = limit → todavía pasa
    assert limiter.check_and_increment("a", "c", limit) is None

    # Tercera llamada: counter=3 > limit → breach
    signal = limiter.check_and_increment("a", "c", limit)
    assert signal is not None
    assert signal.counter == 3

    # Exactamente en t=1030 (diferencia == window_seconds): reset
    tiempo_actual = 1030.0
    signal_reset = limiter.check_and_increment("a", "c", limit)
    # Counter vuelve a 1 → NO breach (primera llamada en nueva ventana)
    assert signal_reset is None


# ---------------------------------------------------------------------------
# Independencia de claves
# ---------------------------------------------------------------------------


def test_claves_independientes_agent_id():
    """Distintos agent_id no comparten estado."""
    now = 1000.0
    limiter = FixedWindowRateLimiter(_now=lambda: now)
    limit = 2

    # Tres llamadas de agente_a: las primeras 2 pasan, la 3ra es breach
    limiter.check_and_increment("agente_a", "chat", limit)
    limiter.check_and_increment("agente_a", "chat", limit)
    signal_a = limiter.check_and_increment("agente_a", "chat", limit)
    assert signal_a is not None

    # Primera llamada de agente_b: nuevo estado independiente
    signal_b = limiter.check_and_increment("agente_b", "chat", limit)
    assert signal_b is None


def test_claves_independientes_chat_id():
    """Distintos chat_id no comparten estado."""
    now = 1000.0
    limiter = FixedWindowRateLimiter(_now=lambda: now)
    limit = 2

    # Tres llamadas en chat_1: pasan 2, breach en la 3ra
    limiter.check_and_increment("agente", "chat_1", limit)
    limiter.check_and_increment("agente", "chat_1", limit)
    signal_1 = limiter.check_and_increment("agente", "chat_1", limit)
    assert signal_1 is not None

    # Primera llamada en chat_2: independiente
    signal_2 = limiter.check_and_increment("agente", "chat_2", limit)
    assert signal_2 is None


def test_pares_distintos_no_interfieren():
    """Cuatro pares (agent_id, chat_id) distintos son completamente independientes."""
    now = 1000.0
    limiter = FixedWindowRateLimiter(_now=lambda: now)
    limit = 10

    pares = [("a1", "c1"), ("a1", "c2"), ("a2", "c1"), ("a2", "c2")]
    for ag, ch in pares:
        # Las primeras `limit` llamadas pasan (counter llega hasta limit sin breach)
        for _ in range(10):
            assert limiter.check_and_increment(ag, ch, limit) is None

    # La (limit+1)-ésima supera el límite → breach
    for ag, ch in pares:
        signal = limiter.check_and_increment(ag, ch, limit)
        assert signal is not None  # counter=11 > limit=10
        assert signal.agent_id == ag
        assert signal.chat_id == ch


# ---------------------------------------------------------------------------
# reset() helper
# ---------------------------------------------------------------------------


def test_reset_limpia_estado():
    """reset() elimina el estado del par — siguiente llamada arranca ventana nueva."""
    now = 1000.0
    limiter = FixedWindowRateLimiter(_now=lambda: now)
    limit = 1

    # Producir breach: con limit=1 pasa la primera, la segunda es breach
    limiter.check_and_increment("a", "c", limit)
    assert limiter.check_and_increment("a", "c", limit) is not None

    # Reset
    limiter.reset("a", "c")

    # Siguiente llamada: ventana nueva
    assert limiter.check_and_increment("a", "c", limit) is None
