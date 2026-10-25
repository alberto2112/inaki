"""Unit tests para BackgroundTask y BackgroundTaskView (REQ-BGD-4).

BackgroundTask es la entidad in-memory que representa una delegación encolada en el
BackgroundDelegationQueueAdapter. BackgroundTaskView es el DTO read-only que
expone el adapter via snapshot_inflight, con un prompt_preview truncado a 80
chars (con elipsis Unicode "…") para inyectar en el system prompt del padre.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from inaki.kernel.domain.background_task import (
    BackgroundTask,
    BackgroundTaskView,
    CancelOutcome,
    DelegationFailure,
    EnqueuedTask,
    InFlightToolCall,
    InputFilesRejected,
    TaskInspection,
    TranscriptEntry,
    WorkdirEntry,
    WorkdirState,
)
from inaki.shared.message import Role


def _make_task(
    *,
    id: str = "bg-1",
    prompt: str = "investigá el saldo de Galicia",
    started_at: datetime | None = None,
    status: str = "queued",
) -> BackgroundTask:
    return BackgroundTask(
        id=id,
        caller_agent_id="inaki",
        target_agent_id="researcher",
        prompt=prompt,
        system_prompt=None,
        channel="telegram",
        chat_id="42",
        max_iterations=10,
        timeout_seconds=60,
        started_at=started_at or datetime(2026, 5, 14, 10, 0, 0, tzinfo=timezone.utc),
        status=status,  # type: ignore[arg-type]
    )


class TestBackgroundTask:
    def test_construye_con_todos_los_campos(self) -> None:
        task = _make_task()

        assert task.id == "bg-1"
        assert task.caller_agent_id == "inaki"
        assert task.target_agent_id == "researcher"
        assert task.prompt == "investigá el saldo de Galicia"
        assert task.system_prompt is None
        assert task.channel == "telegram"
        assert task.chat_id == "42"
        assert task.status == "queued"

    def test_status_solo_acepta_valores_literales(self) -> None:
        # status="completed" no está en el Literal — pydantic debe rechazarlo
        with pytest.raises(ValidationError):
            _make_task(status="completed")

    def test_es_mutable_para_transicion_de_estado(self) -> None:
        """El consumer del adapter muta status de 'queued' a 'running' en sitio."""
        task = _make_task(status="queued")

        task.status = "running"

        assert task.status == "running"


class TestBackgroundTaskViewFromTask:
    """Factory `from_task` construye un DTO truncando el prompt y calculando elapsed.

    Es la única forma documentada de obtener un View desde un Task — encapsula
    las reglas de truncado y elapsed.
    """

    def test_prompt_corto_se_mantiene_intacto(self) -> None:
        task = _make_task(prompt="dame el saldo")
        now = task.started_at + timedelta(seconds=5)

        view = BackgroundTaskView.from_task(task, now=now)

        assert view.prompt_preview == "dame el saldo"

    def test_prompt_de_exactamente_80_chars_se_mantiene_intacto(self) -> None:
        prompt_80 = "a" * 80
        task = _make_task(prompt=prompt_80)
        now = task.started_at + timedelta(seconds=1)

        view = BackgroundTaskView.from_task(task, now=now)

        assert view.prompt_preview == prompt_80
        assert len(view.prompt_preview) == 80

    def test_prompt_de_81_chars_se_trunca_con_elipsis(self) -> None:
        prompt_81 = "a" * 81
        task = _make_task(prompt=prompt_81)
        now = task.started_at + timedelta(seconds=1)

        view = BackgroundTaskView.from_task(task, now=now)

        # 79 chars + "…" = 80 caracteres totales
        assert view.prompt_preview == ("a" * 79) + "…"
        assert len(view.prompt_preview) == 80

    def test_prompt_muy_largo_se_trunca_a_80_con_elipsis(self) -> None:
        prompt_largo = "investigá esto en muchas fuentes y avisame con detalle " * 10
        task = _make_task(prompt=prompt_largo)
        now = task.started_at + timedelta(seconds=1)

        view = BackgroundTaskView.from_task(task, now=now)

        assert len(view.prompt_preview) == 80
        assert view.prompt_preview.endswith("…")
        assert view.prompt_preview[:-1] == prompt_largo[:79]

    def test_elapsed_seconds_truncado_a_int(self) -> None:
        task = _make_task()
        now = task.started_at + timedelta(seconds=42, milliseconds=750)

        view = BackgroundTaskView.from_task(task, now=now)

        assert view.elapsed_seconds == 42

    def test_elapsed_seconds_cero_cuando_now_igual_started_at(self) -> None:
        task = _make_task()
        now = task.started_at

        view = BackgroundTaskView.from_task(task, now=now)

        assert view.elapsed_seconds == 0

    def test_view_propaga_id_target_y_status(self) -> None:
        task = _make_task(id="bg-7", status="running")
        now = task.started_at + timedelta(seconds=10)

        view = BackgroundTaskView.from_task(task, now=now)

        assert view.id == "bg-7"
        assert view.target_agent_id == "researcher"
        assert view.status == "running"


# ---------------------------------------------------------------------------
# bg-delegation-transcript — estados, fallo clasificado, inspección
# ---------------------------------------------------------------------------

_TODOS_LOS_ESTADOS = (
    "queued",
    "running",
    "delivering",
    "delivery_failed",
    "delivered",
    "cancelled",
    "dismissed",
    "interrupted",
)


class TestEstadosPersistidos:
    """REQ-BGP-4 / REQ-BGD-9: ocho estados; los terminales ya no se purgan."""

    @pytest.mark.parametrize("estado", _TODOS_LOS_ESTADOS)
    def test_la_task_acepta_los_ocho_estados(self, estado: str) -> None:
        assert _make_task(status=estado).status == estado

    def test_campos_nuevos_tienen_default_y_no_rompen_construccion_existente(self) -> None:
        task = _make_task()

        assert task.run_started_at is None
        assert task.finished_at is None
        assert task.iterations_used is None
        assert task.result_status is None
        assert task.result_parse_error is False
        assert task.failure is None
        # Una task nueva en memoria no debe avisarse tras un restart: solo las
        # barridas como interrumpidas se marcan notified=False.
        assert task.notified is True

    @pytest.mark.parametrize("estado", ["queued", "running", "delivering", "delivery_failed"])
    def test_view_acepta_los_estados_vivos(self, estado: str) -> None:
        task = _make_task(status=estado)

        view = BackgroundTaskView.from_task(task, now=task.started_at)

        assert view.status == estado

    def test_view_acepta_interrupted_pendiente_de_aviso(self) -> None:
        """REQ-BGD-4 S1: una interrumpida sin avisar sigue visible para el padre."""
        task = _make_task(status="interrupted")

        view = BackgroundTaskView.from_task(task, now=task.started_at)

        assert view.status == "interrupted"

    @pytest.mark.parametrize("estado", ["delivered", "cancelled", "dismissed"])
    def test_view_rechaza_los_terminales(self, estado: str) -> None:
        """REQ-BGD-4 S2: lo terminal se consulta con inspect, nunca viaja al prompt."""
        task = _make_task(status=estado)

        with pytest.raises(ValidationError):
            BackgroundTaskView.from_task(task, now=task.started_at)


class TestDelegationFailure:
    def test_detail_y_kind_son_obligatorios_el_resto_tiene_default(self) -> None:
        falla = DelegationFailure(kind="timeout", detail="TimeoutError (no message)")

        assert falla.iterations_used == 0
        assert falla.elapsed_seconds == 0
        assert falla.last_step == ""
        assert falla.in_flight_tool is None
        assert falla.not_started_tools == []

    def test_kind_solo_acepta_los_cinco_literales(self) -> None:
        with pytest.raises(ValidationError):
            DelegationFailure(kind="boom", detail="x")  # type: ignore[arg-type]

    def test_roundtrip_json_conserva_la_tool_en_vuelo(self) -> None:
        """El store guarda ``failure_json``: lo que entra tiene que salir igual."""
        falla = DelegationFailure(
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

        vuelta = DelegationFailure.model_validate_json(falla.model_dump_json())

        assert vuelta == falla
        assert vuelta.in_flight_tool is not None
        assert vuelta.in_flight_tool.name == "shell_exec"
        assert vuelta.not_started_tools == ["write_file"]

    def test_la_task_serializa_su_failure(self) -> None:
        task = _make_task(status="delivery_failed")
        task.failure = DelegationFailure(kind="exception", detail="RuntimeError (no message)")

        copia = BackgroundTask.model_validate(task.model_dump())

        assert copia.failure is not None
        assert copia.failure.kind == "exception"


class TestCancelOutcomeFinished:
    def test_finished_lleva_el_estado_terminal_de_la_task(self) -> None:
        res = CancelOutcome(outcome="finished", task_id="bg-1000", status="delivered")

        assert res.outcome == "finished"
        assert res.status == "delivered"

    def test_status_es_opcional_para_los_otros_desenlaces(self) -> None:
        res = CancelOutcome(outcome="not_found", task_id="bg-1")

        assert res.status is None


class TestTaskInspection:
    def test_agrupa_task_pasos_y_si_esta_persistida(self) -> None:
        paso = TranscriptEntry(
            seq=1,
            role=Role.USER,
            content="investigá X",
            created_at=datetime(2026, 5, 14, 10, 0, 1, tzinfo=timezone.utc),
        )

        insp = TaskInspection(
            task=_make_task(id="bg-1000"), messages=[paso], messages_total=4, persisted=True
        )

        assert insp.task.id == "bg-1000"
        assert insp.messages[0].role is Role.USER
        assert insp.messages_total == 4
        assert insp.persisted is True
        # defaults del paso: sin tool_calls ni recorte
        assert paso.tool_calls is None
        assert paso.tool_call_id is None
        assert paso.original_chars is None


class TestTranscriptIncompleto:
    """El flag de transcript cortado viaja en la task (persistido) y en la inspección."""

    def test_una_task_nueva_no_esta_incompleta(self) -> None:
        assert _make_task().transcript_incomplete is False

    def test_la_inspeccion_lo_expone_con_default_falso(self) -> None:
        sin = TaskInspection(task=_make_task(), messages=[], messages_total=0, persisted=True)
        con = TaskInspection(
            task=_make_task(),
            messages=[],
            messages_total=0,
            persisted=True,
            transcript_incomplete=True,
        )

        assert sin.transcript_incomplete is False
        assert con.transcript_incomplete is True


class TestWorkdirEnBackgroundTask:
    """REQ-BGW-3 / REQ-BGW-14: la fila guarda la ruta absoluta y los basenames de los insumos."""

    def test_sin_workdir_ni_insumos_por_defecto(self) -> None:
        task = _make_task()

        assert task.workdir is None
        assert task.input_files == []

    def test_guarda_workdir_e_insumos_y_los_serializa(self) -> None:
        task = _make_task()
        task.workdir = "/home/pi/ws/dwss/x7k2p9qa"
        task.input_files = ["a.md", "b.csv"]

        copia = BackgroundTask.model_validate(task.model_dump())

        assert copia.workdir == "/home/pi/ws/dwss/x7k2p9qa"
        assert copia.input_files == ["a.md", "b.csv"]

    def test_la_lista_de_insumos_no_se_comparte_entre_instancias(self) -> None:
        una, otra = _make_task(), _make_task()

        una.input_files.append("a.md")

        assert otra.input_files == []


class TestEnqueuedTask:
    def test_defaults_sin_workdir(self) -> None:
        res = EnqueuedTask(task_id="bg-1000")

        assert res.task_id == "bg-1000"
        assert res.workdir is None
        assert res.workdir_unavailable is False
        assert res.input_files == []

    def test_con_workdir_e_insumos(self) -> None:
        res = EnqueuedTask(
            task_id="bg-1001", workdir="/ws/dwss/abcd1234", input_files=["a.md", "b.csv"]
        )

        assert res.workdir == "/ws/dwss/abcd1234"
        assert res.input_files == ["a.md", "b.csv"]

    def test_workdir_unavailable_marca_que_no_se_pudo_crear(self) -> None:
        res = EnqueuedTask(task_id="bg-m1", workdir_unavailable=True)

        assert res.workdir is None
        assert res.workdir_unavailable is True


class TestWorkdirState:
    def test_entrada_de_archivo_con_tamano_y_directorio_sin_tamano(self) -> None:
        archivo = WorkdirEntry(name="informe.md", kind="file", size_bytes=120)
        carpeta = WorkdirEntry(name="datos", kind="dir", size_bytes=None)

        assert archivo.size_bytes == 120
        assert carpeta.size_bytes is None

    def test_tipo_de_entrada_invalido_se_rechaza(self) -> None:
        with pytest.raises(ValidationError):
            WorkdirEntry(name="x", kind="socket", size_bytes=None)  # type: ignore[arg-type]

    def test_estado_de_un_workdir_existente(self) -> None:
        estado = WorkdirState(
            path="/ws/dwss/abcd1234",
            relative_path="dwss/abcd1234/",
            exists=True,
            entries=[WorkdirEntry(name="a.md", kind="file", size_bytes=3)],
            entries_total=1,
        )

        assert estado.exists is True
        assert estado.note is None
        assert estado.entries[0].name == "a.md"

    def test_estado_de_un_workdir_borrado_lleva_la_nota(self) -> None:
        estado = WorkdirState(
            path="/ws/dwss/abcd1234",
            relative_path="dwss/abcd1234/",
            exists=False,
            entries=[],
            entries_total=0,
            note="workdir no longer exists",
        )

        assert estado.exists is False
        assert estado.note == "workdir no longer exists"

    def test_task_inspection_lleva_workdir_opcional(self) -> None:
        sin = TaskInspection(task=_make_task(), messages=[], messages_total=0, persisted=True)
        con = TaskInspection(
            task=_make_task(),
            messages=[],
            messages_total=0,
            persisted=True,
            workdir=WorkdirState(
                path="/ws/dwss/abcd1234",
                relative_path="dwss/abcd1234/",
                exists=True,
                entries=[],
                entries_total=0,
            ),
        )

        assert sin.workdir is None
        assert con.workdir is not None
        assert con.workdir.relative_path == "dwss/abcd1234/"


class TestInputFilesRejected:
    """REQ-BGW-11..13: el rechazo lleva un ``reason`` estable y la ruta ofensora."""

    def test_lleva_reason_y_path(self) -> None:
        exc = InputFilesRejected("input_file_not_found", "notas/falta.md")

        assert exc.reason == "input_file_not_found"
        assert exc.path == "notas/falta.md"

    def test_el_mensaje_esta_en_ingles_y_nombra_la_ruta(self) -> None:
        exc = InputFilesRejected("input_file_outside_workspace", "../../etc/passwd")

        assert "outside your workspace" in str(exc)
        assert "../../etc/passwd" in str(exc)

    def test_es_una_excepcion(self) -> None:
        with pytest.raises(InputFilesRejected) as info:
            raise InputFilesRejected("input_file_not_a_file", "notas")

        assert "notas" in str(info.value)

    @pytest.mark.parametrize(
        ("reason", "fragmento"),
        [
            ("input_files_require_background", "background mode"),
            ("input_files_require_workdir", "grant_workdir"),
            ("input_file_outside_workspace", "outside your workspace"),
            ("input_file_not_found", "not found"),
            ("input_file_not_a_file", "not a regular file"),
            ("input_file_name_collision", "share the name"),
            ("input_files_too_many", "Too many"),
            ("input_files_too_large", "too large"),
            ("input_files_workdir_unavailable", "working directory"),
            ("input_file_copy_failed", "copy"),
        ],
    )
    def test_cada_reason_tiene_su_mensaje_por_defecto(self, reason: str, fragmento: str) -> None:
        exc = InputFilesRejected(reason, "x.md")  # type: ignore[arg-type]

        assert fragmento in str(exc)

    def test_el_mensaje_puede_darse_explicito(self) -> None:
        exc = InputFilesRejected("input_files_too_many", message="Too many input files (21 > 20).")

        assert str(exc) == "Too many input files (21 > 20)."
        assert exc.path is None

    def test_reason_desconocido_cae_con_valueerror(self) -> None:
        with pytest.raises(ValueError):
            InputFilesRejected("nope")  # type: ignore[arg-type]
