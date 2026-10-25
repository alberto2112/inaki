"""Tests de ``failure_report``: clasificación y render honesto de fallos de delegación.

Todo puro (sin I/O ni store): REQ-BGP-5 (fallo clasificado, nunca con texto vacío),
REQ-BGP-6 (tool en vuelo: efecto desconocido, jamás "no se ejecutó") y REQ-BGD-11
(aviso coalescido post-arranque, con las entregas barridas como línea aparte).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from inaki.agents.delegation.failure_report import (
    clasificar_fallo,
    fallo_unknown_target,
    render_aviso_interrupcion,
    render_fallo,
    tool_calls_sin_par,
    ultimo_paso,
)
from inaki.kernel.domain.background_task import (
    BackgroundTask,
    DelegationFailure,
    InFlightToolCall,
)
from inaki.shared.errors import ToolLoopMaxIterationsError
from inaki.shared.message import Message, Role

_T0 = datetime(2026, 10, 25, 9, 0, 0, tzinfo=timezone.utc)


_WORKDIR = "/home/pi/.inaki/workspace/dwss/x7k2p9qa"


def _task(
    *,
    id: str = "bg-1003",
    target: str = "researcher",
    prompt: str = "investigá el saldo",
    status: str = "running",
    timeout_seconds: int = 300,
    max_iterations: int = 10,
    run_started_at: datetime | None = None,
    finished_at: datetime | None = None,
    iterations_used: int | None = None,
    failure: DelegationFailure | None = None,
    workdir: str | None = None,
) -> BackgroundTask:
    return BackgroundTask(
        id=id,
        caller_agent_id="inaki",
        target_agent_id=target,
        prompt=prompt,
        system_prompt=None,
        channel="telegram",
        chat_id="42",
        max_iterations=max_iterations,
        timeout_seconds=timeout_seconds,
        started_at=_T0,
        status=status,  # type: ignore[arg-type]
        run_started_at=run_started_at,
        finished_at=finished_at,
        iterations_used=iterations_used,
        failure=failure,
        workdir=workdir,
    )


def _user(text: str = "tarea") -> Message:
    return Message(role=Role.USER, content=text)


def _llama(*calls: tuple[str, str, str]) -> Message:
    """assistant con tool_calls: cada call es (id, nombre, arguments)."""
    return Message(
        role=Role.ASSISTANT,
        content="",
        tool_calls=[
            {"id": cid, "function": {"name": name, "arguments": args}} for cid, name, args in calls
        ],
    )


def _resultado(call_id: str, texto: str = "ok") -> Message:
    return Message(role=Role.TOOL, content=texto, tool_call_id=call_id)


def _dijo(texto: str) -> Message:
    return Message(role=Role.ASSISTANT, content=texto)


# ---------------------------------------------------------------------------
# tool_calls_sin_par
# ---------------------------------------------------------------------------


class TestToolCallsSinPar:
    def test_todo_par_no_deja_nada_en_vuelo(self) -> None:
        trace = [
            _user(),
            _llama(("c1", "web_fetch", "{}")),
            _resultado("c1"),
            _dijo("listo"),
        ]

        res = tool_calls_sin_par(trace)

        assert res.en_vuelo is None
        assert res.no_iniciadas == []

    def test_un_batch_cortado_la_primera_sin_resultado_esta_en_vuelo_el_resto_no_arranco(
        self,
    ) -> None:
        trace = [
            _user(),
            _llama(
                ("c1", "shell_exec", '{"cmd": "ls"}'),
                ("c2", "write_file", "{}"),
                ("c3", "send_email", "{}"),
            ),
        ]

        res = tool_calls_sin_par(trace)

        assert res.en_vuelo == InFlightToolCall(
            tool_call_id="c1", name="shell_exec", arguments_preview='{"cmd": "ls"}'
        )
        assert res.no_iniciadas == ["write_file", "send_email"]

    def test_las_que_ya_tienen_resultado_no_cuentan(self) -> None:
        trace = [
            _user(),
            _llama(
                ("c1", "web_fetch", "{}"), ("c2", "shell_exec", "{}"), ("c3", "write_file", "{}")
            ),
            _resultado("c1"),
        ]

        res = tool_calls_sin_par(trace)

        assert res.en_vuelo is not None
        assert res.en_vuelo.name == "shell_exec"
        assert res.no_iniciadas == ["write_file"]

    def test_los_argumentos_largos_se_recortan_a_200_con_elipsis(self) -> None:
        trace = [_user(), _llama(("c1", "write_file", "x" * 500))]

        res = tool_calls_sin_par(trace)

        assert res.en_vuelo is not None
        assert len(res.en_vuelo.arguments_preview) == 200
        assert res.en_vuelo.arguments_preview.endswith("…")

    def test_argumentos_dict_se_serializan(self) -> None:
        trace = [
            _user(),
            Message(
                role=Role.ASSISTANT,
                content="",
                tool_calls=[{"id": "c1", "function": {"name": "t", "arguments": {"a": 1}}}],
            ),
        ]

        res = tool_calls_sin_par(trace)

        assert res.en_vuelo is not None
        assert res.en_vuelo.arguments_preview == '{"a": 1}'

    def test_trace_vacio(self) -> None:
        res = tool_calls_sin_par([])

        assert res.en_vuelo is None
        assert res.no_iniciadas == []


# ---------------------------------------------------------------------------
# ultimo_paso
# ---------------------------------------------------------------------------


class TestUltimoPaso:
    def test_sin_respuesta_del_llm_todavia(self) -> None:
        assert ultimo_paso([_user()]) == "no LLM response yet"
        assert ultimo_paso([]) == "no LLM response yet"

    def test_ultimo_mensaje_con_tool_calls_nombra_las_tools(self) -> None:
        trace = [_user(), _llama(("c1", "web_fetch", "{}"), ("c2", "shell_exec", "{}"))]

        assert ultimo_paso(trace) == "called web_fetch, shell_exec"

    def test_ultimo_mensaje_resultado_nombra_la_tool_que_respondio(self) -> None:
        trace = [_user(), _llama(("c1", "web_fetch", "{}")), _resultado("c1")]

        assert ultimo_paso(trace) == "received the result of web_fetch"

    def test_ultimo_mensaje_texto_lo_cita_recortado_a_200(self) -> None:
        trace = [_user(), _dijo("a" * 300)]

        paso = ultimo_paso(trace)

        assert paso.startswith("said: «")
        assert paso.endswith("…»")
        assert len(paso) == len("said: «") + 200 + len("»")


# ---------------------------------------------------------------------------
# clasificar_fallo
# ---------------------------------------------------------------------------


class TestClasificarFallo:
    def test_timeout_vacio_nunca_deja_el_detalle_en_blanco(self) -> None:
        """El bug original: ``TimeoutError('')`` se reportaba como ``failed: TimeoutError: ``."""
        trace = [_user(), _llama(("c1", "shell_exec", '{"cmd": "sleep 999"}'))]

        falla = clasificar_fallo(asyncio.TimeoutError(""), trace, _task(), 300.4)

        assert falla.kind == "timeout"
        assert falla.detail == "TimeoutError (no message)"
        assert falla.elapsed_seconds == 300
        assert falla.iterations_used == 1
        assert falla.last_step == "called shell_exec"
        assert falla.in_flight_tool is not None
        assert falla.in_flight_tool.name == "shell_exec"

    def test_excepcion_con_mensaje_lleva_tipo_y_mensaje(self) -> None:
        falla = clasificar_fallo(RuntimeError("boom"), [_user()], _task(), 3)

        assert falla.kind == "exception"
        assert falla.detail == "RuntimeError: boom"

    def test_excepcion_sin_mensaje_lo_dice_explicitamente(self) -> None:
        """REQ-BGP-5 S3."""
        assert clasificar_fallo(RuntimeError(""), [_user()], _task(), 1).detail == (
            "RuntimeError (no message)"
        )
        assert clasificar_fallo(ValueError("   "), [_user()], _task(), 1).detail == (
            "ValueError (no message)"
        )

    def test_max_iterations_usa_el_tope_y_cita_la_ultima_respuesta(self) -> None:
        """REQ-BGP-5 S2: iteraciones = tope; la última respuesta del hijo viaja en el reporte."""
        trace = [_user(), _llama(("c1", "web_fetch", "{}")), _resultado("c1")]
        exc = ToolLoopMaxIterationsError(last_response="todavía no termino")

        falla = clasificar_fallo(exc, trace, _task(max_iterations=7), 42)

        assert falla.kind == "max_iterations"
        assert falla.iterations_used == 7
        assert falla.last_step == "said: «todavía no termino»"
        assert falla.detail.startswith("ToolLoopMaxIterationsError")
        assert falla.in_flight_tool is None

    def test_max_iterations_sin_ultima_respuesta_cae_al_paso_del_trace(self) -> None:
        trace = [_user(), _llama(("c1", "web_fetch", "{}")), _resultado("c1")]

        falla = clasificar_fallo(ToolLoopMaxIterationsError(""), trace, _task(), 1)

        assert falla.last_step == "received the result of web_fetch"

    def test_timeout_con_el_batch_cortado_separa_en_vuelo_de_no_iniciadas(self) -> None:
        trace = [_user(), _llama(("c1", "shell_exec", "{}"), ("c2", "write_file", "{}"))]

        falla = clasificar_fallo(TimeoutError(), trace, _task(), 300)

        assert falla.in_flight_tool is not None
        assert falla.in_flight_tool.name == "shell_exec"
        assert falla.not_started_tools == ["write_file"]

    def test_todo_par_no_marca_tool_en_vuelo(self) -> None:
        trace = [_user(), _llama(("c1", "web_fetch", "{}")), _resultado("c1")]

        falla = clasificar_fallo(TimeoutError(), trace, _task(), 300)

        assert falla.in_flight_tool is None
        assert falla.not_started_tools == []

    def test_unknown_target(self) -> None:
        falla = fallo_unknown_target("fantasma")

        assert falla.kind == "unknown_target"
        assert "fantasma" in falla.detail


# ---------------------------------------------------------------------------
# render_fallo
# ---------------------------------------------------------------------------


class TestRenderFallo:
    def _falla_timeout(self) -> DelegationFailure:
        return DelegationFailure(
            kind="timeout",
            detail="TimeoutError (no message)",
            iterations_used=3,
            elapsed_seconds=300,
            last_step="called shell_exec",
            in_flight_tool=InFlightToolCall(
                tool_call_id="c1", name="shell_exec", arguments_preview='{"cmd": "ls"}'
            ),
            not_started_tools=["write_file"],
        )

    def test_timeout_a_mitad_de_tool_declara_efecto_desconocido(self) -> None:
        """REQ-BGP-6 S1: NUNCA afirma que la tool no se ejecutó."""
        out = render_fallo(_task(), self._falla_timeout())

        assert out.startswith(
            "[bg-1003] failed (timeout): the child agent ran out of time (300s) before finishing."
        )
        assert "- agent: researcher" in out
        assert "- budget: 300s / 10 iterations — used 300s / 3 iterations" in out
        assert "- last step: called shell_exec" in out
        assert (
            '- tool call in flight: shell_exec({"cmd": "ls"}) — outcome unknown, '
            "it may have had side effects. Verify before retrying." in out
        )
        assert "- not started: write_file" in out
        assert out.rstrip().endswith(
            "Inspect the full transcript with background_tasks(action=inspect, task_id=bg-1003)."
        )

    def test_todo_par_omite_las_lineas_de_tool_en_vuelo_y_no_iniciadas(self) -> None:
        """REQ-BGP-6 S2."""
        falla = DelegationFailure(
            kind="timeout",
            detail="TimeoutError (no message)",
            iterations_used=2,
            elapsed_seconds=300,
            last_step="received the result of web_fetch",
        )

        out = render_fallo(_task(), falla)

        assert "outcome unknown" not in out
        assert "tool call in flight" not in out
        assert "not started" not in out
        assert "- last step: received the result of web_fetch" in out

    def test_excepcion_sin_mensaje_nombra_el_tipo_y_dice_que_no_hubo_mensaje(self) -> None:
        falla = DelegationFailure(kind="exception", detail="RuntimeError (no message)")

        out = render_fallo(_task(), falla)

        assert out.startswith(
            "[bg-1003] failed (exception): the child agent stopped with an error."
        )
        assert "- error: RuntimeError (no message)" in out

    def test_max_iterations_habla_del_tope_y_de_la_ultima_respuesta(self) -> None:
        falla = DelegationFailure(
            kind="max_iterations",
            detail="ToolLoopMaxIterationsError: reached the limit of 10 iterations",
            iterations_used=10,
            elapsed_seconds=80,
            last_step="said: «todavía no termino»",
        )

        out = render_fallo(_task(max_iterations=10), falla)

        assert "the child agent used all 10 iterations without a final answer." in out
        assert "- budget: 300s / 10 iterations — used 80s / 10 iterations" in out
        assert "- last step: said: «todavía no termino»" in out

    def test_unknown_target_no_trae_transcript_ni_pista_de_inspect(self) -> None:
        out = render_fallo(_task(target="fantasma"), fallo_unknown_target("fantasma"))

        assert out.startswith(
            "[bg-1003] failed (unknown_target): 'fantasma' is not a known sub-agent."
        )
        assert "inspect" not in out
        assert "budget" not in out

    def test_sin_last_step_la_linea_se_omite(self) -> None:
        falla = DelegationFailure(kind="exception", detail="OSError: x")

        assert "last step" not in render_fallo(_task(), falla)

    # -- workdir (REQ-BGW-10) -------------------------------------------------

    def test_con_workdir_lo_menciona_antes_del_hint_de_inspect(self) -> None:
        """REQ-BGW-10 S1: el padre puede rescatar artefactos parciales."""
        out = render_fallo(_task(workdir=_WORKDIR), self._falla_timeout())

        lineas = out.splitlines()
        assert (
            "- workdir: dwss/x7k2p9qa/ — partial outputs may be there; inspect lists its contents."
            in lineas
        )
        assert lineas[-1].startswith("Inspect the full transcript")  # el hint sigue al final
        assert (
            lineas.index(
                "- workdir: dwss/x7k2p9qa/ — partial outputs may be there; inspect lists its contents."
            )
            < len(lineas) - 1
        )

    @pytest.mark.parametrize(
        "falla",
        [
            DelegationFailure(kind="exception", detail="OSError: x", last_step="called a"),
            DelegationFailure(kind="max_iterations", detail="ToolLoopMaxIterationsError: x"),
        ],
        ids=["exception", "max_iterations"],
    )
    def test_el_workdir_se_menciona_en_todo_fallo_que_corrio(self, falla) -> None:
        out = render_fallo(_task(workdir=_WORKDIR), falla)

        assert "- workdir: dwss/x7k2p9qa/" in out

    def test_sin_workdir_el_texto_es_identico_al_de_siempre(self) -> None:
        falla = self._falla_timeout()

        con = render_fallo(_task(workdir=_WORKDIR), falla)
        sin = render_fallo(_task(), falla)

        assert "workdir" not in sin
        assert (
            con.replace(
                "- workdir: dwss/x7k2p9qa/ — partial outputs may be there; inspect lists its "
                "contents.\n",
                "",
            )
            == sin
        )


# ---------------------------------------------------------------------------
# render_aviso_interrupcion
# ---------------------------------------------------------------------------


def _interrumpida(
    id: str = "bg-1001",
    *,
    target: str = "researcher",
    prompt: str = "investigá X",
    corrio: bool = True,
    en_vuelo: str | None = "web_fetch",
    workdir: str | None = None,
) -> BackgroundTask:
    if not corrio:
        return _task(id=id, target=target, prompt=prompt, status="interrupted", workdir=workdir)
    return _task(
        id=id,
        target=target,
        prompt=prompt,
        workdir=workdir,
        status="interrupted",
        run_started_at=_T0,
        finished_at=_T0 + timedelta(seconds=42),
        iterations_used=3,
        failure=DelegationFailure(
            kind="interrupted",
            detail="the daemon stopped (reload or shutdown)",
            last_step=f"called {en_vuelo}" if en_vuelo else "received the result of web_fetch",
            in_flight_tool=InFlightToolCall(
                tool_call_id="c1", name=en_vuelo, arguments_preview="{}"
            )
            if en_vuelo
            else None,
        ),
    )


def _entrega_barrida(
    id: str = "bg-1002", target: str = "writer", workdir: str | None = None
) -> BackgroundTask:
    return _task(
        id=id, target=target, status="delivery_failed", prompt="redactá el informe", workdir=workdir
    )


class TestRenderAvisoInterrupcion:
    def test_una_task_que_corria_lista_tiempo_iteraciones_y_tool_en_vuelo(self) -> None:
        out = render_aviso_interrupcion([_interrumpida()], [])

        assert out.startswith("[bg-1001] interrupted:")
        assert "They will NOT deliver a result on their own." in out
        assert '- bg-1001 → researcher | was running for 42s, 3 iterations | "investigá X"' in out
        assert "last step: called web_fetch" in out
        assert "tool call in flight: web_fetch" in out
        assert "outcome unknown, it may have had side effects" in out
        assert "background_tasks(action=inspect, task_id=...)" in out
        assert "a relaunch repeats any side effects" in out
        # Sin entregas barridas no aparece nada de cancel/entrega.
        assert "delivery" not in out
        assert "action=cancel" not in out

    def test_una_task_que_nunca_arranco_lo_dice(self) -> None:
        out = render_aviso_interrupcion([_interrumpida(corrio=False)], [])

        assert "- bg-1001 → researcher | never started (it was queued)" in out
        assert "outcome unknown" not in out

    def test_sin_tool_en_vuelo_no_afirma_efectos(self) -> None:
        out = render_aviso_interrupcion([_interrumpida(en_vuelo=None)], [])

        assert "last step: received the result of web_fetch" in out
        assert "outcome unknown" not in out

    def test_varias_interrumpidas_van_en_un_solo_aviso_con_todos_los_ids(self) -> None:
        out = render_aviso_interrupcion(
            [_interrumpida("bg-1001"), _interrumpida("bg-1003", target="writer", corrio=False)], []
        )

        assert out.startswith("[bg-1001, bg-1003] interrupted:")
        assert "- bg-1001 → researcher" in out
        assert "- bg-1003 → writer" in out

    def test_las_entregas_barridas_van_en_linea_aparte_con_el_hint_de_cancel(self) -> None:
        """R3 / REQ-BGD-11: delivering→delivery_failed se lista aparte, recuperable con cancel."""
        out = render_aviso_interrupcion([_interrumpida()], [_entrega_barrida()])

        assert "- bg-1001 → researcher" in out
        linea = next(ln for ln in out.splitlines() if ln.startswith("- bg-1002"))
        assert "writer" in linea
        assert "finished" in linea
        assert "may or may not have reached you" in linea
        assert "background_tasks(action=cancel, task_id=bg-1002)" in linea
        # No se mezcla con el encabezado de las interrumpidas.
        assert out.startswith("[bg-1001] interrupted:")
        assert "bg-1002" not in out.splitlines()[0]

    def test_solo_entregas_barridas_no_inventa_interrupcion(self) -> None:
        out = render_aviso_interrupcion(
            [], [_entrega_barrida("bg-1002"), _entrega_barrida("bg-1004")]
        )

        assert "interrupted:" not in out
        assert out.startswith("[bg-1002, bg-1004] finished, result not delivered:")
        assert "action=cancel, task_id=bg-1002" in out
        assert "action=cancel, task_id=bg-1004" in out
        assert "Tell the user" in out

    def test_el_prompt_se_recorta_a_80(self) -> None:
        out = render_aviso_interrupcion([_interrumpida(prompt="p" * 200)], [])

        linea = next(ln for ln in out.splitlines() if ln.startswith("- bg-1001"))
        assert '"' + "p" * 79 + '…"' in linea

    # -- workdir (REQ-BGW-10 S2) ----------------------------------------------

    def test_solo_la_linea_de_la_task_con_workdir_lo_menciona(self) -> None:
        out = render_aviso_interrupcion(
            [_interrumpida("bg-1001", workdir=_WORKDIR), _interrumpida("bg-1003", target="writer")],
            [],
        )

        con = next(ln for ln in out.splitlines() if ln.startswith("- bg-1001"))
        sin = next(ln for ln in out.splitlines() if ln.startswith("- bg-1003"))
        assert con.endswith(" | workdir: dwss/x7k2p9qa/")
        assert "workdir" not in sin

    def test_la_entrega_barrida_con_workdir_tambien_lo_menciona(self) -> None:
        out = render_aviso_interrupcion([], [_entrega_barrida(workdir=_WORKDIR)])

        linea = next(ln for ln in out.splitlines() if ln.startswith("- bg-1002"))
        assert linea.endswith(" | workdir: dwss/x7k2p9qa/")

    def test_sin_workdir_en_ninguna_task_el_aviso_no_cambia(self) -> None:
        out = render_aviso_interrupcion([_interrumpida()], [_entrega_barrida()])

        assert "workdir" not in out

    def test_sin_nada_que_avisar_es_un_error_de_uso(self) -> None:
        with pytest.raises(ValueError):
            render_aviso_interrupcion([], [])
