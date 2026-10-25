"""Tests de ``GestorDeWorkdirs`` (filesystem real en ``tmp_path``).

Cubre REQ-BGW-2 (creación con ``secrets`` y colisiones), REQ-BGW-6 (borrado con la
compuerta de seguridad), REQ-BGW-7 (barrido de huérfanos), REQ-BGW-9 (``describir``) y
REQ-BGW-4 (texto para el hijo). La preparación de ``input_files`` (REQ-BGW-12/13) vive en
``TestPreparar``.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

import pytest

from inaki.agents.delegation import workdirs
from inaki.kernel.domain.background_task import InputFilesRejected
from inaki.agents.delegation.workdirs import (
    MAX_ENTRADAS,
    MAX_INPUT_BYTES,
    MAX_INPUT_FILES,
    MAX_INTENTOS,
    NOMBRE_RE,
    GestorDeWorkdirs,
    instruccion_para_el_hijo,
    ruta_relativa,
)

_LOGGER = "inaki.agents.delegation.workdirs"


@pytest.fixture
def raiz(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


@pytest.fixture
def gestor(raiz: Path) -> GestorDeWorkdirs:
    return GestorDeWorkdirs(workspaces={"ana": raiz}, con_workdir=["ana"])


def _forzar_nombres(monkeypatch: pytest.MonkeyPatch, *nombres: str) -> list[int]:
    """Hace que el generador de nombres devuelva ``nombres`` en orden (y repita el último)."""
    llamadas: list[int] = []

    def falso() -> str:
        llamadas.append(1)
        return nombres[min(len(llamadas), len(nombres)) - 1]

    monkeypatch.setattr(workdirs, "_nombre_aleatorio", falso)
    return llamadas


# ---------------------------------------------------------------------------
# tiene_workdir / crear (REQ-BGW-2)
# ---------------------------------------------------------------------------


class TestCrear:
    def test_con_el_flag_crea_dwss_nombre_dentro_de_la_raiz(
        self, gestor: GestorDeWorkdirs, raiz: Path
    ) -> None:
        ruta = gestor.crear("ana")

        assert ruta is not None
        assert ruta.is_dir()
        assert ruta.is_absolute()
        assert ruta.parent == raiz / "dwss"
        assert NOMBRE_RE.fullmatch(ruta.name)

    def test_sin_el_flag_no_crea_nada_y_devuelve_none(self, raiz: Path) -> None:
        gestor = GestorDeWorkdirs(workspaces={"ana": raiz, "bob": raiz}, con_workdir=["ana"])

        assert gestor.crear("bob") is None
        assert not (raiz / "dwss").exists()

    def test_un_caller_desconocido_devuelve_none(self, gestor: GestorDeWorkdirs) -> None:
        assert gestor.crear("nadie") is None

    def test_tiene_workdir_exige_el_flag_y_un_workspace(self, raiz: Path) -> None:
        gestor = GestorDeWorkdirs(
            workspaces={"ana": raiz, "bob": raiz}, con_workdir=["ana", "fantasma"]
        )

        assert gestor.tiene_workdir("ana") is True
        assert gestor.tiene_workdir("bob") is False  # tiene workspace pero no el flag
        assert gestor.tiene_workdir("fantasma") is False  # tiene el flag pero no workspace

    def test_dos_creaciones_dan_directorios_distintos(self, gestor: GestorDeWorkdirs) -> None:
        a, b = gestor.crear("ana"), gestor.crear("ana")

        assert a is not None and b is not None
        assert a != b

    def test_el_nombre_sale_del_alfabeto_a_z_0_9(self, gestor: GestorDeWorkdirs) -> None:
        for _ in range(30):
            ruta = gestor.crear("ana")
            assert ruta is not None
            assert re.fullmatch(r"[a-z0-9]{8}", ruta.name)

    def test_una_colision_reintenta_con_otro_nombre_y_deja_intacto_el_existente(
        self, gestor: GestorDeWorkdirs, raiz: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """REQ-BGW-2 S2."""
        existente = raiz / "dwss" / "aaaaaaaa"
        existente.mkdir(parents=True)
        (existente / "mio.txt").write_text("no me toques")
        llamadas = _forzar_nombres(monkeypatch, "aaaaaaaa", "aaaaaaaa", "bbbbbbbb")

        ruta = gestor.crear("ana")

        assert ruta == raiz / "dwss" / "bbbbbbbb"
        assert len(llamadas) == 3
        assert (existente / "mio.txt").read_text() == "no me toques"

    def test_intentos_agotados_lanza_oserror(
        self, gestor: GestorDeWorkdirs, raiz: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (raiz / "dwss" / "aaaaaaaa").mkdir(parents=True)
        llamadas = _forzar_nombres(monkeypatch, "aaaaaaaa")

        with pytest.raises(OSError, match="aaaaaaaa|intentos"):
            gestor.crear("ana")

        assert len(llamadas) == MAX_INTENTOS

    def test_dwss_no_escribible_lanza_oserror(self, gestor: GestorDeWorkdirs, raiz: Path) -> None:
        """REQ-BGW-2 S4 (la cola lo degrada): ``dwss`` existe pero es un fichero."""
        (raiz / "dwss").write_text("no soy un directorio")

        with pytest.raises(OSError):
            gestor.crear("ana")

    def test_dwss_symlink_lanza_oserror_y_no_crea_nada_en_el_destino(
        self, gestor: GestorDeWorkdirs, raiz: Path, tmp_path: Path
    ) -> None:
        afuera = tmp_path / "afuera"
        afuera.mkdir()
        (raiz / "dwss").symlink_to(afuera, target_is_directory=True)

        with pytest.raises(OSError, match="es un symlink"):
            gestor.crear("ana")

        assert list(afuera.iterdir()) == []


# ---------------------------------------------------------------------------
# borrar — la compuerta de seguridad (REQ-BGW-6)
# ---------------------------------------------------------------------------


class TestBorrar:
    def test_borra_un_workdir_valido_con_todo_su_contenido(
        self, gestor: GestorDeWorkdirs, tmp_path: Path
    ) -> None:
        ruta = gestor.crear("ana")
        assert ruta is not None
        (ruta / "sub").mkdir()
        (ruta / "sub" / "x.txt").write_text("x")
        (ruta / "a.md").write_text("a")
        afuera = tmp_path / "afuera.txt"
        afuera.write_text("sobrevivo")
        (ruta / "enlace").symlink_to(afuera)  # rmtree no sigue symlinks internos

        gestor.borrar("bg-1000", str(ruta))

        assert not ruta.exists()
        assert afuera.read_text() == "sobrevivo"

    def test_un_directorio_inexistente_es_un_no_op_sin_error(
        self, gestor: GestorDeWorkdirs, raiz: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.DEBUG, logger=_LOGGER)

        gestor.borrar("bg-1000", str(raiz / "dwss" / "abcd1234"))

        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    @pytest.mark.parametrize(
        ("construir", "motivo"),
        [
            pytest.param(lambda r: Path("dwss/abcd1234"), "absoluta", id="ruta-relativa"),
            pytest.param(lambda r: r / "notas" / "abcd1234", "dwss", id="padre-no-es-dwss"),
            pytest.param(lambda r: r / "notas", "dwss", id="ruta-de-notas-del-usuario"),
            pytest.param(lambda r: r / "dwss" / "Mis-Notas", "nombre", id="nombre-con-formato"),
            pytest.param(lambda r: r / "dwss" / "abcdefg", "nombre", id="nombre-de-7"),
            pytest.param(lambda r: r / "dwss" / "abcdefghi", "nombre", id="nombre-de-9"),
            pytest.param(lambda r: r / "dwss" / "ABCD1234", "nombre", id="mayusculas"),
        ],
    )
    def test_la_compuerta_rechaza_y_no_borra_nada(
        self,
        gestor: GestorDeWorkdirs,
        raiz: Path,
        caplog: pytest.LogCaptureFixture,
        construir,
        motivo: str,
    ) -> None:
        """REQ-BGW-6 S2: fila con una ruta que no pasa la compuerta → dir intacto + ERROR."""
        ruta = construir(raiz)
        if ruta.is_absolute():
            ruta.mkdir(parents=True)
            (ruta / "precioso.txt").write_text("no me borres")
        caplog.set_level(logging.ERROR, logger=_LOGGER)

        gestor.borrar("bg-1000", str(ruta))

        if ruta.is_absolute():
            assert (ruta / "precioso.txt").read_text() == "no me borres"
        errores = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errores) == 1
        assert str(ruta) in errores[0].getMessage()
        assert motivo in errores[0].getMessage()

    def test_un_symlink_no_se_sigue_ni_se_borra(
        self, gestor: GestorDeWorkdirs, raiz: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """REQ-BGW-6 S3."""
        destino = tmp_path / "notas-reales"
        destino.mkdir()
        (destino / "importante.txt").write_text("vivo")
        enlace = raiz / "dwss" / "abcd1234"
        enlace.parent.mkdir()
        enlace.symlink_to(destino, target_is_directory=True)
        caplog.set_level(logging.ERROR, logger=_LOGGER)

        gestor.borrar("bg-1000", str(enlace))

        assert enlace.is_symlink()
        assert (destino / "importante.txt").read_text() == "vivo"
        errores = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errores) == 1
        assert "es un symlink" in errores[0].getMessage()
        assert "NO se borra" in errores[0].getMessage()

    def test_un_dwss_symlink_no_se_sigue_aunque_el_hijo_tenga_nombre_valido(
        self, gestor: GestorDeWorkdirs, raiz: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Review: ``<ws>/dwss`` → ``/otro`` haría que ``<ws>/dwss/abcd1234`` apunte (por el
        padre) a ``/otro/abcd1234``. ``ruta.is_symlink()`` no lo ve: el symlink es el PADRE."""
        otro = tmp_path / "otro"
        victima = otro / "abcd1234"
        victima.mkdir(parents=True)
        (victima / "vivo.txt").write_text("no me borres")
        (raiz / "dwss").symlink_to(otro, target_is_directory=True)
        caplog.set_level(logging.ERROR, logger=_LOGGER)

        gestor.borrar("bg-1000", str(raiz / "dwss" / "abcd1234"))

        assert (victima / "vivo.txt").read_text() == "no me borres"
        errores = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errores) == 1
        assert "NO se borra" in errores[0].getMessage()
        assert "symlink" in errores[0].getMessage()

    def test_un_dwss_real_con_hijo_valido_sigue_borrando(
        self, gestor: GestorDeWorkdirs, raiz: Path
    ) -> None:
        """Triangulación: el endurecimiento del padre no rechaza lo legítimo."""
        ruta = gestor.crear("ana")
        assert ruta is not None
        assert not (raiz / "dwss").is_symlink()

        gestor.borrar("bg-1000", str(ruta))

        assert not ruta.exists()

    @pytest.mark.parametrize(
        "sufijo",
        [
            pytest.param("x/../dwss/abcd1234", id="sube-antes-del-dwss"),
            pytest.param("dwss/../dwss/abcd1234", id="rodea-el-dwss"),
        ],
    )
    def test_cualquier_punto_punto_en_la_ruta_se_rechaza(
        self, gestor: GestorDeWorkdirs, raiz: Path, caplog: pytest.LogCaptureFixture, sufijo: str
    ) -> None:
        """Review: ``Path`` no normaliza ``..``; la ruta guardada se evalúa tal cual y un ``..``
        la hace apuntar a un sitio distinto del que dice su nombre."""
        ruta = Path(f"{raiz}/{sufijo}")
        # Existe de verdad (a través de los ``..``) para que el rechazo no sea por inexistencia.
        (raiz / "x").mkdir(exist_ok=True)
        (raiz / "dwss").mkdir(exist_ok=True)
        real = raiz / "dwss" / "abcd1234"
        real.mkdir(exist_ok=True)
        (real / "vivo.txt").write_text("no me borres")
        assert ".." in ruta.parts
        caplog.set_level(logging.ERROR, logger=_LOGGER)

        gestor.borrar("bg-1000", str(ruta))

        assert (real / "vivo.txt").read_text() == "no me borres"
        errores = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errores) == 1
        assert "NO se borra" in errores[0].getMessage()
        assert ".." in errores[0].getMessage()

    def test_si_rmtree_falla_loguea_error_y_no_lanza(
        self,
        gestor: GestorDeWorkdirs,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """REQ-BGW-6 S4: el arranque no se cae por un directorio que no se pudo borrar."""
        ruta = gestor.crear("ana")
        assert ruta is not None

        def explota(*_a, **_k):
            raise PermissionError("sin permisos")

        monkeypatch.setattr(workdirs.shutil, "rmtree", explota)
        caplog.set_level(logging.ERROR, logger=_LOGGER)

        gestor.borrar("bg-1000", str(ruta))  # no lanza

        assert ruta.is_dir()
        errores = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errores) == 1
        assert str(ruta) in errores[0].getMessage()
        assert "PermissionError" in errores[0].getMessage()

    def test_el_error_nombra_la_task(
        self, gestor: GestorDeWorkdirs, raiz: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.ERROR, logger=_LOGGER)

        gestor.borrar("bg-1234", str(raiz / "notas"))

        assert "bg-1234" in caplog.text


# ---------------------------------------------------------------------------
# barrer — huérfanos (REQ-BGW-7)
# ---------------------------------------------------------------------------


class TestBarrer:
    def test_borra_el_huerfano_y_respeta_referenciados_y_en_creacion(
        self, gestor: GestorDeWorkdirs, raiz: Path
    ) -> None:
        huerfano = raiz / "dwss" / "huerfano"
        referenciado = raiz / "dwss" / "ref00001"
        en_curso = raiz / "dwss" / "enc00001"
        for d in (huerfano, referenciado, en_curso):
            d.mkdir(parents=True)
            (d / "x").write_text("x")

        borrados = gestor.barrer({str(referenciado)}, en_creacion={str(en_curso)})

        assert borrados == 1
        assert not huerfano.exists()
        assert referenciado.is_dir()
        assert en_curso.is_dir()

    def test_ignora_lo_que_no_cumple_el_patron(self, gestor: GestorDeWorkdirs, raiz: Path) -> None:
        """REQ-BGW-7 S2: ``dwss/mis-notas`` NO se toca."""
        notas = raiz / "dwss" / "mis-notas"
        notas.mkdir(parents=True)
        (notas / "a.md").write_text("a")
        (raiz / "dwss" / "ABCD1234").mkdir()
        (raiz / "dwss" / "suelto.txt").write_text("x")

        borrados = gestor.barrer(set(), en_creacion=set())

        assert borrados == 0
        assert (notas / "a.md").read_text() == "a"
        assert (raiz / "dwss" / "ABCD1234").is_dir()
        assert (raiz / "dwss" / "suelto.txt").exists()

    def test_un_fichero_o_symlink_con_nombre_valido_no_se_toca(
        self, gestor: GestorDeWorkdirs, raiz: Path, tmp_path: Path
    ) -> None:
        (raiz / "dwss").mkdir()
        (raiz / "dwss" / "abcd1234").write_text("soy un fichero")
        afuera = tmp_path / "afuera"
        afuera.mkdir()
        (afuera / "x").write_text("x")
        (raiz / "dwss" / "efgh5678").symlink_to(afuera, target_is_directory=True)

        borrados = gestor.barrer(set(), en_creacion=set())

        assert borrados == 0
        assert (raiz / "dwss" / "abcd1234").read_text() == "soy un fichero"
        assert (afuera / "x").exists()

    def test_sin_dwss_no_hay_nada_que_barrer(self, gestor: GestorDeWorkdirs) -> None:
        assert gestor.barrer(set(), en_creacion=set()) == 0

    def test_dwss_symlink_se_loguea_error_y_no_se_recorre(
        self,
        gestor: GestorDeWorkdirs,
        raiz: Path,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        afuera = tmp_path / "afuera"
        (afuera / "abcd1234").mkdir(parents=True)
        (raiz / "dwss").symlink_to(afuera, target_is_directory=True)
        caplog.set_level(logging.ERROR, logger=_LOGGER)

        borrados = gestor.barrer(set(), en_creacion=set())

        assert borrados == 0
        assert (afuera / "abcd1234").is_dir()
        assert any("es un symlink" in r.getMessage() for r in caplog.records)

    def test_recorre_la_raiz_de_cada_caller_conocido_aunque_no_tenga_flag(
        self, tmp_path: Path
    ) -> None:
        r1, r2 = tmp_path / "w1", tmp_path / "w2"
        for r in (r1, r2):
            (r / "dwss" / "huerfano").mkdir(parents=True)
        gestor = GestorDeWorkdirs(workspaces={"ana": r1, "bob": r2}, con_workdir=["ana"])

        borrados = gestor.barrer(set(), en_creacion=set())

        assert borrados == 2
        assert not (r1 / "dwss" / "huerfano").exists()
        assert not (r2 / "dwss" / "huerfano").exists()

    def test_dos_callers_con_la_misma_raiz_la_barren_una_sola_vez(self, raiz: Path) -> None:
        (raiz / "dwss" / "huerfano").mkdir(parents=True)
        gestor = GestorDeWorkdirs(workspaces={"ana": raiz, "bob": raiz}, con_workdir=["ana"])

        assert gestor.barrer(set(), en_creacion=set()) == 1

    def test_un_workdir_bajo_otra_raiz_no_se_toca_aunque_este_huerfano(
        self, gestor: GestorDeWorkdirs, tmp_path: Path
    ) -> None:
        """REQ-BGW-8 S2: huérfano bajo un workspace viejo → no se barre (riesgo aceptado)."""
        viejo = tmp_path / "w-viejo" / "dwss" / "huerfano"
        viejo.mkdir(parents=True)

        assert gestor.barrer(set(), en_creacion=set()) == 0
        assert viejo.is_dir()

    def test_un_fallo_al_borrar_uno_no_impide_barrer_el_resto(
        self,
        gestor: GestorDeWorkdirs,
        raiz: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        for nombre in ("aaaaaaaa", "bbbbbbbb"):
            (raiz / "dwss" / nombre).mkdir(parents=True)
        real = shutil.rmtree

        def a_veces(path, *a, **k):
            if Path(path).name == "aaaaaaaa":
                raise PermissionError("no")
            return real(path, *a, **k)

        monkeypatch.setattr(workdirs.shutil, "rmtree", a_veces)
        caplog.set_level(logging.ERROR, logger=_LOGGER)

        borrados = gestor.barrer(set(), en_creacion=set())

        assert borrados == 1
        assert (raiz / "dwss" / "aaaaaaaa").is_dir()
        assert not (raiz / "dwss" / "bbbbbbbb").exists()


# ---------------------------------------------------------------------------
# describir (REQ-BGW-9)
# ---------------------------------------------------------------------------


class TestDescribir:
    def test_lista_los_ficheros_con_nombre_tipo_y_tamano(
        self, gestor: GestorDeWorkdirs, raiz: Path
    ) -> None:
        """REQ-BGW-9 S1."""
        ruta = gestor.crear("ana")
        assert ruta is not None
        (ruta / "b.csv").write_text("12345")
        (ruta / "a.md").write_text("abc")
        (ruta / "carpeta").mkdir()

        estado = gestor.describir(str(ruta))

        assert estado.exists is True
        assert estado.path == str(ruta)
        assert estado.relative_path == f"dwss/{ruta.name}/"
        assert estado.note is None
        assert estado.entries_total == 3
        assert [(e.name, e.kind, e.size_bytes) for e in estado.entries] == [
            ("a.md", "file", 3),
            ("b.csv", "file", 5),
            ("carpeta", "dir", None),
        ]

    def test_un_symlink_interno_figura_como_other_sin_seguirlo(
        self, gestor: GestorDeWorkdirs, tmp_path: Path
    ) -> None:
        ruta = gestor.crear("ana")
        assert ruta is not None
        (tmp_path / "afuera.txt").write_text("hola")
        (ruta / "enlace").symlink_to(tmp_path / "afuera.txt")

        estado = gestor.describir(str(ruta))

        assert [(e.name, e.kind, e.size_bytes) for e in estado.entries] == [
            ("enlace", "other", None)
        ]

    def test_el_listado_se_acota_a_20_pero_cuenta_el_total(self, gestor: GestorDeWorkdirs) -> None:
        ruta = gestor.crear("ana")
        assert ruta is not None
        for i in range(25):
            (ruta / f"f{i:02d}.txt").write_text("x")

        estado = gestor.describir(str(ruta))

        assert MAX_ENTRADAS == 20
        assert len(estado.entries) == 20
        assert estado.entries_total == 25
        assert estado.entries[0].name == "f00.txt"

    def test_un_workdir_vacio_existe_con_cero_entradas(self, gestor: GestorDeWorkdirs) -> None:
        ruta = gestor.crear("ana")
        assert ruta is not None

        estado = gestor.describir(str(ruta))

        assert estado.exists is True
        assert estado.entries == []
        assert estado.entries_total == 0

    def test_un_directorio_borrado_dice_que_ya_no_existe_sin_fingir_contenido(
        self, gestor: GestorDeWorkdirs, raiz: Path
    ) -> None:
        """REQ-BGW-9 S2."""
        ruta = raiz / "dwss" / "abcd1234"

        estado = gestor.describir(str(ruta))

        assert estado.exists is False
        assert estado.entries == []
        assert estado.entries_total == 0
        assert estado.note == "workdir no longer exists"
        assert estado.relative_path == "dwss/abcd1234/"

    def test_una_ruta_que_no_pasa_la_compuerta_no_se_lista(
        self, gestor: GestorDeWorkdirs, raiz: Path
    ) -> None:
        notas = raiz / "notas"
        notas.mkdir()
        (notas / "secreto.txt").write_text("x")

        estado = gestor.describir(str(notas))

        assert estado.entries == []
        assert estado.entries_total == 0
        assert estado.note == "unexpected workdir path; not listed"

    def test_si_el_listado_falla_lo_dice_en_la_nota_y_no_lanza(
        self, gestor: GestorDeWorkdirs, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ruta = gestor.crear("ana")
        assert ruta is not None

        def explota(_path):
            raise PermissionError("no")

        monkeypatch.setattr(workdirs.os, "scandir", explota)

        estado = gestor.describir(str(ruta))

        assert estado.exists is True
        assert estado.entries == []
        assert estado.note is not None
        assert "PermissionError" in estado.note

    def test_describir_no_crea_ni_borra_nada(self, gestor: GestorDeWorkdirs, raiz: Path) -> None:
        gestor.describir(str(raiz / "dwss" / "abcd1234"))

        assert not (raiz / "dwss").exists()


# ---------------------------------------------------------------------------
# Helpers de texto (REQ-BGW-4)
# ---------------------------------------------------------------------------


class TestTextoParaElHijo:
    def test_ruta_relativa_es_dwss_nombre_con_barra_final(self) -> None:
        assert ruta_relativa("/home/pi/ws/dwss/x7k2p9qa") == "dwss/x7k2p9qa/"

    def test_instruccion_nombra_la_relativa_y_la_absoluta(self) -> None:
        texto = instruccion_para_el_hijo("/home/pi/ws/dwss/x7k2p9qa", [])

        assert texto.startswith("\n\n---\n")
        assert "`dwss/x7k2p9qa/`" in texto
        assert "`/home/pi/ws/dwss/x7k2p9qa`" in texto
        assert "Shell commands do not start in it" in texto
        assert "Input files" not in texto

    def test_instruccion_con_insumos_agrega_la_linea(self) -> None:
        texto = instruccion_para_el_hijo("/ws/dwss/x7k2p9qa", ["a.md", "b.csv"])

        assert (
            "Input files provided for this task: a.md, b.csv (in your working directory)." in texto
        )

    def test_la_linea_de_insumos_va_aparte_de_la_instruccion(self) -> None:
        texto = instruccion_para_el_hijo("/ws/dwss/x7k2p9qa", ["a.md"])

        lineas = texto.strip().splitlines()
        assert lineas[-1].startswith("Input files provided")
        assert not lineas[-2].startswith("Input files provided")


# ---------------------------------------------------------------------------
# preparar — input_files (REQ-BGW-12 / REQ-BGW-13)
# ---------------------------------------------------------------------------


def _fichero(raiz: Path, rel: str, contenido: str = "contenido") -> Path:
    destino = raiz / rel
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(contenido)
    return destino


def _rechazo(gestor: GestorDeWorkdirs, archivos: list[str]) -> InputFilesRejected:
    with pytest.raises(InputFilesRejected) as info:
        gestor.preparar("ana", archivos)
    return info.value


class TestPreparar:
    def test_los_topes_son_20_ficheros_y_50_mib(self) -> None:
        assert MAX_INPUT_FILES == 20
        assert MAX_INPUT_BYTES == 50 * 1024 * 1024

    def test_exito_copia_plano_con_el_basename_y_contenido_identico(
        self, gestor: GestorDeWorkdirs, raiz: Path
    ) -> None:
        """REQ-BGW-13 S1."""
        a = _fichero(raiz, "notas/a.md", "# hola")
        b = _fichero(raiz, "datos/b.csv", "x,y\n1,2\n")

        listo = gestor.preparar("ana", ["notas/a.md", str(b)])

        assert listo.input_files == ["a.md", "b.csv"]
        assert listo.ruta.parent == raiz / "dwss"
        assert NOMBRE_RE.fullmatch(listo.ruta.name)
        assert (listo.ruta / "a.md").read_text() == "# hola"
        assert (listo.ruta / "b.csv").read_text() == "x,y\n1,2\n"
        assert sorted(p.name for p in listo.ruta.iterdir()) == ["a.md", "b.csv"]
        # los originales siguen donde estaban, sin tocar
        assert a.read_text() == "# hola"
        assert b.read_text() == "x,y\n1,2\n"

    def test_acepta_rutas_relativas_al_workspace_y_absolutas_dentro_de_el(
        self, gestor: GestorDeWorkdirs, raiz: Path
    ) -> None:
        _fichero(raiz, "r.txt")
        abs_ = _fichero(raiz, "sub/a.txt")

        listo = gestor.preparar("ana", ["r.txt", str(abs_)])

        assert listo.input_files == ["r.txt", "a.txt"]

    def test_un_symlink_dentro_del_workspace_a_otro_fichero_dentro_se_copia(
        self, gestor: GestorDeWorkdirs, raiz: Path
    ) -> None:
        real = _fichero(raiz, "real.md", "dato")
        (raiz / "enlace.md").symlink_to(real)

        listo = gestor.preparar("ana", ["enlace.md"])

        assert listo.input_files == ["real.md"]
        assert (listo.ruta / "real.md").read_text() == "dato"

    def test_una_lista_vacia_crea_el_workdir_sin_ficheros(self, gestor: GestorDeWorkdirs) -> None:
        listo = gestor.preparar("ana", [])

        assert listo.ruta.is_dir()
        assert listo.input_files == []
        assert list(listo.ruta.iterdir()) == []

    def test_sin_el_flag_rechaza_con_input_files_require_workdir(self, raiz: Path) -> None:
        gestor = GestorDeWorkdirs(workspaces={"ana": raiz}, con_workdir=[])
        _fichero(raiz, "a.md")

        with pytest.raises(InputFilesRejected) as info:
            gestor.preparar("ana", ["a.md"])

        assert info.value.reason == "input_files_require_workdir"
        assert not (raiz / "dwss").exists()

    # -- validación previa: nada se crea (REQ-BGW-12) -------------------------

    def test_fuera_del_workspace_con_dotdot(self, gestor: GestorDeWorkdirs, raiz: Path) -> None:
        """REQ-BGW-12 S1."""
        exc = _rechazo(gestor, ["../../etc/passwd"])

        assert exc.reason == "input_file_outside_workspace"
        assert exc.path == "../../etc/passwd"
        assert "../../etc/passwd" in str(exc)
        assert not (raiz / "dwss").exists()

    def test_ruta_absoluta_fuera_del_workspace(
        self, gestor: GestorDeWorkdirs, raiz: Path, tmp_path: Path
    ) -> None:
        """REQ-BGW-12 S4: la contención es SIEMPRE estricta (el gestor ni la recibe)."""
        afuera = tmp_path / "secreto.txt"
        afuera.write_text("credenciales")

        exc = _rechazo(gestor, [str(afuera)])

        assert exc.reason == "input_file_outside_workspace"
        assert exc.path == str(afuera)
        assert not (raiz / "dwss").exists()

    def test_symlink_dentro_que_apunta_afuera(
        self, gestor: GestorDeWorkdirs, raiz: Path, tmp_path: Path
    ) -> None:
        """REQ-BGW-12 S5: el symlink se resuelve ANTES de chequear la contención."""
        afuera = tmp_path / "secreto.txt"
        afuera.write_text("credenciales")
        (raiz / "inocente.txt").symlink_to(afuera)

        exc = _rechazo(gestor, ["inocente.txt"])

        assert exc.reason == "input_file_outside_workspace"
        assert exc.path == "inocente.txt"
        assert not (raiz / "dwss").exists()

    def test_fichero_inexistente(self, gestor: GestorDeWorkdirs, raiz: Path) -> None:
        exc = _rechazo(gestor, ["notas/falta.md"])

        assert exc.reason == "input_file_not_found"
        assert exc.path == "notas/falta.md"
        assert "notas/falta.md" in str(exc)
        assert not (raiz / "dwss").exists()

    def test_un_directorio_no_es_un_fichero(self, gestor: GestorDeWorkdirs, raiz: Path) -> None:
        """REQ-BGW-12 S2."""
        (raiz / "notas").mkdir()

        exc = _rechazo(gestor, ["notas"])

        assert exc.reason == "input_file_not_a_file"
        assert exc.path == "notas"
        assert not (raiz / "dwss").exists()

    def test_basenames_repetidos_en_carpetas_distintas(
        self, gestor: GestorDeWorkdirs, raiz: Path
    ) -> None:
        """REQ-BGW-12 S3."""
        _fichero(raiz, "a/x.md")
        _fichero(raiz, "b/x.md")

        exc = _rechazo(gestor, ["a/x.md", "b/x.md"])

        assert exc.reason == "input_file_name_collision"
        assert exc.path == "x.md"
        assert "x.md" in str(exc)
        assert not (raiz / "dwss").exists()

    def test_la_colision_ignora_mayusculas_porque_el_fs_puede_ser_case_insensitive(
        self, gestor: GestorDeWorkdirs, raiz: Path
    ) -> None:
        _fichero(raiz, "a/x.md")
        _fichero(raiz, "b/X.MD")

        assert _rechazo(gestor, ["a/x.md", "b/X.MD"]).reason == "input_file_name_collision"

    def test_el_mismo_fichero_dos_veces_es_una_colision(
        self, gestor: GestorDeWorkdirs, raiz: Path
    ) -> None:
        _fichero(raiz, "a.md")

        assert _rechazo(gestor, ["a.md", "./a.md"]).reason == "input_file_name_collision"

    def test_mas_de_20_ficheros(self, gestor: GestorDeWorkdirs, raiz: Path) -> None:
        nombres = [f"f{i:02d}.txt" for i in range(21)]
        for n in nombres:
            _fichero(raiz, n)

        exc = _rechazo(gestor, nombres)

        assert exc.reason == "input_files_too_many"
        assert "21" in str(exc) and "20" in str(exc)
        assert not (raiz / "dwss").exists()

    def test_exactamente_20_ficheros_se_aceptan(self, gestor: GestorDeWorkdirs, raiz: Path) -> None:
        nombres = [f"f{i:02d}.txt" for i in range(20)]
        for n in nombres:
            _fichero(raiz, n)

        listo = gestor.preparar("ana", nombres)

        assert listo.input_files == nombres

    def test_un_fichero_mayor_a_50_mib(self, gestor: GestorDeWorkdirs, raiz: Path) -> None:
        """Con el tope REAL: un fichero disperso de 50 MiB + 1 byte (no ocupa disco)."""
        grande = raiz / "grande.bin"
        with grande.open("wb") as fh:
            fh.truncate(MAX_INPUT_BYTES + 1)

        exc = _rechazo(gestor, ["grande.bin"])

        assert exc.reason == "input_files_too_large"
        assert "50" in str(exc)
        assert not (raiz / "dwss").exists()

    def test_el_tope_de_tamano_es_sobre_la_suma_y_el_limite_es_inclusivo(
        self, gestor: GestorDeWorkdirs, raiz: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(workdirs, "MAX_INPUT_BYTES", 10)
        _fichero(raiz, "a.txt", "12345")
        _fichero(raiz, "b.txt", "12345")
        _fichero(raiz, "c.txt", "123456")

        listo = gestor.preparar("ana", ["a.txt", "b.txt"])  # 10 bytes: justo en el tope
        assert listo.input_files == ["a.txt", "b.txt"]

        assert _rechazo(gestor, ["a.txt", "c.txt"]).reason == "input_files_too_large"  # 11 bytes

    def test_un_error_en_el_segundo_fichero_no_deja_nada_creado(
        self, gestor: GestorDeWorkdirs, raiz: Path
    ) -> None:
        """Se valida TODO antes de crear: el primero (válido) no deja un dir huérfano."""
        _fichero(raiz, "bueno.md")

        exc = _rechazo(gestor, ["bueno.md", "../fuera.md"])

        assert exc.reason == "input_file_outside_workspace"
        assert not (raiz / "dwss").exists()

    # -- crear y copiar, con rollback (REQ-BGW-13) -----------------------------

    def test_dwss_no_escribible_con_insumos_rechaza_y_no_deja_nada(
        self, gestor: GestorDeWorkdirs, raiz: Path
    ) -> None:
        """REQ-BGW-13 S3."""
        _fichero(raiz, "a.md")
        (raiz / "dwss").write_text("no soy un directorio")

        exc = _rechazo(gestor, ["a.md"])

        assert exc.reason == "input_files_workdir_unavailable"
        assert (raiz / "dwss").read_text() == "no soy un directorio"

    def test_un_fichero_que_desaparece_entre_validar_y_copiar_borra_el_workdir(
        self, gestor: GestorDeWorkdirs, raiz: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """REQ-BGW-13 S2: el 2º fichero desaparece → rollback, sin task."""
        _fichero(raiz, "a.md")
        segundo = _fichero(raiz, "b.md")
        real = shutil.copy2
        llamadas: list[Path] = []

        def copia_con_carrera(src, dst, *a, **k):
            llamadas.append(Path(src))
            if len(llamadas) == 2:
                Path(src).unlink()  # otro proceso lo borró justo antes de copiarlo
            return real(src, dst, *a, **k)

        monkeypatch.setattr(workdirs.shutil, "copy2", copia_con_carrera)

        exc = _rechazo(gestor, ["a.md", "b.md"])

        assert exc.reason == "input_file_copy_failed"
        assert exc.path == "b.md"
        assert "b.md" in str(exc)
        assert len(llamadas) == 2
        assert not segundo.exists()
        # el workdir y el primer fichero ya copiado se eliminaron
        assert list((raiz / "dwss").iterdir()) == []

    def test_el_rollback_pasa_por_la_compuerta_y_no_toca_los_originales(
        self, gestor: GestorDeWorkdirs, raiz: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original = _fichero(raiz, "a.md", "intacto")

        def explota(*_a, **_k):
            raise PermissionError("disco de solo lectura")

        monkeypatch.setattr(workdirs.shutil, "copy2", explota)

        exc = _rechazo(gestor, ["a.md"])

        assert exc.reason == "input_file_copy_failed"
        assert original.read_text() == "intacto"
        assert list((raiz / "dwss").iterdir()) == []
