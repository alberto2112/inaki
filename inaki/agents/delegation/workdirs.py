"""GestorDeWorkdirs — directorios de trabajo propios de las delegaciones en background.

Con ``delegation.grant_workdir`` el que delega recibe, por cada delegación ``wait=false``,
un directorio ``<workspace del caller>/dwss/<8 chars a-z0-9>`` donde el hijo deja lo que
produce (y donde se copian los ``input_files`` ANTES de encolar). Este módulo es el ÚNICO
dueño del ciclo de vida de esos directorios: crearlos, borrarlos, barrer los huérfanos y
describirlos para ``inspect``.

Decisiones (ver ``bg-delegation-transcript``, delta de workdir):

- La ruta que se persiste en la fila es ABSOLUTA. Un cambio de ``workspace.path`` o un
  caller borrado no deja dirs inborrables ni hace que ``inspect`` mire el lugar equivocado.
  A cambio, el borrado confía en un path leído de disco: por eso pasa SIEMPRE por la
  compuerta de ``_motivo_rechazo`` (absoluta, sin ``..``, ``parent.name == "dwss"``,
  nombre ``^[a-z0-9]{8}$``, ni la ruta ni su padre son symlink) antes de tocar nada con ``rmtree``.
- ``borrar`` NUNCA lanza: un directorio que no se pudo borrar es un ERROR en el log, no un
  arranque caído.
- La contención del hijo es solo por prompt (``instruccion_para_el_hijo``): usa las tools
  del caller, que resuelven contra su workspace entero.

El módulo solo conoce stdlib, ``inaki.shared`` y el dominio del kernel: ``agents`` no importa
``inaki.tools`` ni ``inaki.config``.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import shutil
import string
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from inaki.kernel.domain.background_task import InputFilesRejected, WorkdirEntry, WorkdirState
from inaki.shared.path_resolution import WorkspaceEscapeError, resolve_path

logger = logging.getLogger(__name__)

# Subdirectorio del workspace que agrupa los workdirs, y forma estricta del nombre de cada uno.
SUBDIR = "dwss"
NOMBRE_RE = re.compile(r"^[a-z0-9]{8}$")
_ALFABETO = string.ascii_lowercase + string.digits
_LARGO_NOMBRE = 8

# Intentos de crear un nombre libre antes de rendirse (con 36^8 nombres, agotarlos significa
# que algo anda mal en el disco, no mala suerte).
MAX_INTENTOS = 5

# Tope del listado superficial de ``describir`` (el total real se informa aparte).
MAX_ENTRADAS = 20

# Topes de ``input_files``: la copia DUPLICA bytes en la SD de la Pi y vive lo que vive la
# task. Un insumo grande no necesita copia: el hijo comparte el workspace del caller y puede
# leerlo por su ruta.
MAX_INPUT_FILES = 20
MAX_INPUT_BYTES = 50 * 1024 * 1024
_MIB = 1024 * 1024

NOTA_INEXISTENTE = "workdir no longer exists"
NOTA_RUTA_INESPERADA = "unexpected workdir path; not listed"


# ---------------------------------------------------------------------------
# Helpers puros
# ---------------------------------------------------------------------------


def _nombre_aleatorio() -> str:
    """Nombre de 8 caracteres ``[a-z0-9]`` con ``secrets`` (no predecible por el hijo)."""
    return "".join(secrets.choice(_ALFABETO) for _ in range(_LARGO_NOMBRE))


def _motivo_rechazo(ruta: Path) -> str | None:
    """Compuerta de seguridad previa a cualquier borrado o listado. ``None`` = pasa.

    Se evalúa sobre la ruta TAL CUAL está guardada, sin resolver: resolver un symlink
    escondería justo lo que la compuerta busca.
    """
    if not ruta.is_absolute():
        return "la ruta no es absoluta"
    # ``Path`` no normaliza ``..``: ``/ws/x/../../etc/dwss/abcd1234`` pasaría los chequeos de
    # nombre y apuntaría a otro sitio. Como la ruta se evalúa tal cual, se rechaza.
    if ".." in ruta.parts:
        return "contiene '..' (la ruta no se normaliza)"
    if ruta.parent.name != SUBDIR:
        return f"su directorio padre no es '{SUBDIR}'"
    if not NOMBRE_RE.fullmatch(ruta.name):
        return "el nombre no cumple ^[a-z0-9]{8}$"
    if ruta.is_symlink():
        return "es un symlink (no se sigue)"
    # ``<ws>/dwss`` symlink a ``/`` haría que ``<ws>/dwss/abcd1234`` sea, en realidad,
    # ``/abcd1234``: ``ruta.is_symlink()`` no lo ve porque el enlace es el PADRE.
    if ruta.parent.is_symlink():
        return f"su directorio padre '{SUBDIR}' es un symlink (no se sigue)"
    return None


def ruta_relativa(ruta: str) -> str:
    """``/…/dwss/x7k2p9qa`` → ``dwss/x7k2p9qa/`` (la forma que ven el padre y el hijo)."""
    return f"{SUBDIR}/{Path(ruta).name}/"


def instruccion_para_el_hijo(ruta: str, input_files: Iterable[str]) -> str:
    """Texto (en inglés, lo lee el LLM) que se agrega al prompt de tarea del hijo.

    Le dice dónde crear lo que produzca y de dónde leer sus insumos. Es contención SOLO
    por instrucción: nada impide técnicamente que escriba fuera del directorio.
    """
    texto = (
        f"\n\n---\nWorking directory for this task: `{ruta_relativa(ruta)}` "
        f"(relative to the workspace; absolute path: `{ruta}`). "
        "Create every file you produce there and read your input files from there. "
        "Shell commands do not start in it: use the absolute path. "
        "List the files you created in your result."
    )
    insumos = list(input_files)
    if insumos:
        texto += (
            f"\nInput files provided for this task: {', '.join(insumos)} "
            "(in your working directory)."
        )
    return texto


@dataclass(frozen=True)
class WorkdirPreparado:
    """Workdir ya creado con sus insumos copiados: ``ruta`` absoluta y basenames en orden."""

    ruta: Path
    input_files: list[str]


# ---------------------------------------------------------------------------
# Gestor
# ---------------------------------------------------------------------------


class GestorDeWorkdirs:
    """Ciclo de vida de los workdirs de las delegaciones en background.

    Args:
        workspaces: ``caller_id`` → raíz (``workspace.path`` efectivo) de CADA agente que
            puede delegar. El barrido recorre todas esas raíces.
        con_workdir: ``caller_id`` de los que tienen ``delegation.grant_workdir``.

    Ambos los reparte el composition root al ensamblar; son estáticos porque ``/reload`` y
    ``config set`` reensamblan.
    """

    def __init__(self, workspaces: Mapping[str, Path], con_workdir: Iterable[str]) -> None:
        self._workspaces = {aid: Path(raiz) for aid, raiz in workspaces.items()}
        self._con_workdir = frozenset(con_workdir)

    def tiene_workdir(self, caller_id: str) -> bool:
        """¿El caller tiene el flag Y un workspace donde crear los directorios?"""
        return caller_id in self._con_workdir and caller_id in self._workspaces

    def _raiz(self, caller_id: str) -> Path:
        return self._workspaces[caller_id].resolve()

    # -- crear ---------------------------------------------------------------

    def crear(self, caller_id: str) -> Path | None:
        """Crea ``<raíz>/dwss/<nombre>`` y devuelve su ruta absoluta.

        ``None`` si el caller no tiene workdir (no es un error). Lanza ``OSError`` si no se
        pudo crear (``dwss`` no escribible o symlink, nombres agotados): quien llama decide
        cómo degradar y loguea la capacidad perdida.
        """
        if not self.tiene_workdir(caller_id):
            return None
        base = self._raiz(caller_id) / SUBDIR
        if base.is_symlink():
            raise OSError(f"'{base}' es un symlink; no se crean workdirs ahí")
        base.mkdir(parents=True, exist_ok=True)
        for _ in range(MAX_INTENTOS):
            ruta = base / _nombre_aleatorio()
            try:
                ruta.mkdir(exist_ok=False)
            except FileExistsError:
                continue  # colisión: otro nombre; el existente queda intacto
            return ruta
        raise OSError(f"sin nombre libre en '{base}' tras {MAX_INTENTOS} intentos")

    # -- preparar (input_files) ----------------------------------------------

    def preparar(self, caller_id: str, input_files: Sequence[str]) -> WorkdirPreparado:
        """Valida los insumos, crea el workdir y los COPIA ahí, o lanza sin dejar nada.

        Orden (W12): validar TODO → crear → copiar. Así los errores comunes no crean nada
        y el rollback solo cubre fallos de disco o carreras (un fichero que desaparece entre
        validar y copiar). Cualquier fallo posterior a crear borra el workdir por la
        compuerta de ``borrar``. Los originales jamás se modifican.

        La contención al workspace del caller es SIEMPRE estricta, sea cual sea
        ``workspace.containment``: esto es una copia masiva sin revisión a un directorio que
        lee otro agente; con ``off`` una inyección podría hacer copiar, p. ej., la config con
        credenciales. Es síncrono (E/S de hasta 50 MiB): quien lo llama desde el event loop
        debería moverlo a un hilo.

        Raises:
            InputFilesRejected: con un ``reason`` estable (ver ``InputFilesRejection``).
        """
        if not self.tiene_workdir(caller_id):
            raise InputFilesRejected("input_files_require_workdir")
        origenes = self._validar(caller_id, input_files)

        try:
            destino = self.crear(caller_id)
        except OSError as exc:
            raise InputFilesRejected(
                "input_files_workdir_unavailable", str(self._raiz(caller_id) / SUBDIR)
            ) from exc
        if destino is None:  # inalcanzable: tiene_workdir ya se verificó
            raise InputFilesRejected("input_files_workdir_unavailable")

        try:
            for pedido, origen in zip(input_files, origenes, strict=True):
                try:
                    shutil.copy2(origen, destino / origen.name)
                except OSError as exc:
                    raise InputFilesRejected("input_file_copy_failed", pedido) from exc
        except BaseException:
            self._borrar("(rollback de input_files)", str(destino))
            raise
        return WorkdirPreparado(ruta=destino, input_files=[o.name for o in origenes])

    def _validar(self, caller_id: str, input_files: Sequence[str]) -> list[Path]:
        """Resuelve y valida cada insumo. Devuelve las rutas reales, en el orden pedido."""
        raiz = self._raiz(caller_id)
        origenes: list[Path] = []
        nombres: set[str] = set()
        total_bytes = 0
        for pedido in input_files:
            try:
                resuelta = resolve_path(pedido, raiz, "strict")
                existe = resuelta.exists()
                es_fichero = existe and resuelta.is_file()
                tamano = resuelta.stat().st_size if es_fichero else 0
            except WorkspaceEscapeError as exc:
                raise InputFilesRejected("input_file_outside_workspace", pedido) from exc
            except (OSError, RuntimeError) as exc:
                raise InputFilesRejected("input_file_not_found", pedido) from exc
            if not existe:
                raise InputFilesRejected("input_file_not_found", pedido)
            if not es_fichero:
                raise InputFilesRejected("input_file_not_a_file", pedido)
            if resuelta.name.casefold() in nombres:
                raise InputFilesRejected("input_file_name_collision", resuelta.name)
            nombres.add(resuelta.name.casefold())
            total_bytes += tamano
            origenes.append(resuelta)
        if len(origenes) > MAX_INPUT_FILES:
            raise InputFilesRejected(
                "input_files_too_many",
                message=f"Too many input files ({len(origenes)} > {MAX_INPUT_FILES}).",
            )
        if total_bytes > MAX_INPUT_BYTES:
            raise InputFilesRejected(
                "input_files_too_large",
                message=(
                    f"Input files are too large ({total_bytes / _MIB:.1f} MiB > "
                    f"{MAX_INPUT_BYTES / _MIB:g} MiB)."
                ),
            )
        return origenes

    # -- borrar --------------------------------------------------------------

    def borrar(self, task_id: str, ruta: str) -> None:
        """Borra el workdir ``ruta`` si pasa la compuerta. NUNCA lanza.

        Un directorio ya inexistente no es un error. Si la compuerta rechaza la ruta (p. ej.
        una fila con ``/home/pi/notas``) o ``rmtree`` falla, se loguea ERROR nombrando la
        ruta y NO se borra (o queda a medias): el arranque sigue.
        """
        self._borrar(task_id, ruta)

    def _borrar(self, task_id: str, ruta: str) -> bool:
        """Como ``borrar`` pero informa si el directorio quedó borrado por esta llamada."""
        path = Path(ruta)
        motivo = _motivo_rechazo(path)
        if motivo is not None:
            logger.error(
                "background task workdir: NO se borra '%s' (%s): %s", ruta, task_id, motivo
            )
            return False
        if not path.exists():
            return False
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            return False
        except Exception as exc:  # noqa: BLE001 — borrar no puede tumbar el arranque
            logger.error(
                "background task workdir: no se pudo borrar '%s' (%s): %s: %s",
                ruta,
                task_id,
                type(exc).__name__,
                exc,
            )
            return False
        return True

    # -- barrer --------------------------------------------------------------

    def barrer(self, referenciados: Collection[str], *, en_creacion: Collection[str] = ()) -> int:
        """Borra los workdirs sin fila bajo ``<raíz>/dwss/`` de cada raíz conocida.

        Solo toca entradas que son directorios reales con nombre ``^[a-z0-9]{8}$`` y cuya
        ruta no está en ``referenciados`` (filas) ni en ``en_creacion`` (workdir ya creado
        cuya fila todavía no existe). Todo lo demás (``dwss/mis-notas``) queda intacto.
        Devuelve cuántos borró. NUNCA lanza.
        """
        protegidos = set(referenciados) | set(en_creacion)
        borrados = 0
        raices = {raiz.resolve() for raiz in self._workspaces.values()}
        for raiz in sorted(raices):
            base = raiz / SUBDIR
            if base.is_symlink():
                logger.error(
                    "background task workdir: '%s' es un symlink; no se barren huérfanos ahí",
                    base,
                )
                continue
            if not base.is_dir():
                continue
            try:
                entradas = sorted(base.iterdir())
            except OSError as exc:
                logger.error(
                    "background task workdir: no se pudo listar '%s' para barrer huérfanos: %s: %s",
                    base,
                    type(exc).__name__,
                    exc,
                )
                continue
            for entrada in entradas:
                if not NOMBRE_RE.fullmatch(entrada.name):
                    continue
                if entrada.is_symlink() or not entrada.is_dir():
                    continue
                if str(entrada) in protegidos:
                    continue
                if self._borrar("(huérfano)", str(entrada)):
                    borrados += 1
        return borrados

    # -- describir -----------------------------------------------------------

    def describir(self, ruta: str) -> WorkdirState:
        """Estado superficial del workdir para ``inspect`` (REQ-BGW-9). NUNCA lanza.

        Lista hasta ``MAX_ENTRADAS`` entradas (nombre, tipo, tamaño) y cuenta el total.
        Si el directorio ya no existe lo dice; si la ruta no pasa la compuerta no la lista
        (es un path leído de disco: no se confía en él para listar tampoco).
        """
        path = Path(ruta)
        if _motivo_rechazo(path) is not None:
            return WorkdirState(
                path=ruta,
                relative_path=ruta,
                exists=path.is_dir(),
                entries=[],
                entries_total=0,
                note=NOTA_RUTA_INESPERADA,
            )
        relativa = ruta_relativa(ruta)
        if not path.is_dir():
            return WorkdirState(
                path=ruta,
                relative_path=relativa,
                exists=False,
                entries=[],
                entries_total=0,
                note=NOTA_INEXISTENTE,
            )
        try:
            with os.scandir(path) as it:
                entradas = sorted(it, key=lambda e: e.name)
            descritas = [self._entrada(e) for e in entradas[:MAX_ENTRADAS]]
        except OSError as exc:
            return WorkdirState(
                path=ruta,
                relative_path=relativa,
                exists=True,
                entries=[],
                entries_total=0,
                note=f"could not list the directory ({type(exc).__name__})",
            )
        return WorkdirState(
            path=ruta,
            relative_path=relativa,
            exists=True,
            entries=descritas,
            entries_total=len(entradas),
        )

    @staticmethod
    def _entrada(entrada: os.DirEntry[str]) -> WorkdirEntry:
        # Sin seguir symlinks: un enlace es "other", nunca el fichero o carpeta a la que apunta.
        if entrada.is_symlink():
            return WorkdirEntry(name=entrada.name, kind="other")
        if entrada.is_dir(follow_symlinks=False):
            return WorkdirEntry(name=entrada.name, kind="dir")
        if entrada.is_file(follow_symlinks=False):
            return WorkdirEntry(
                name=entrada.name,
                kind="file",
                size_bytes=entrada.stat(follow_symlinks=False).st_size,
            )
        return WorkdirEntry(name=entrada.name, kind="other")
