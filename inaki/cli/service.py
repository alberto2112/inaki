"""``inaki service install|uninstall`` — la unidad systemd, generada desde el paquete.

Reemplaza a ``systemd/install.sh`` (que vivía en el repo y asumía un venv en
``<repo>/.venv``). La unidad se renderiza con la ruta ABSOLUTA del ``inaki``
que está corriendo este comando, así sirve igual para un venv del repo, para
pipx o para cualquier otro intérprete: el daemon no depende del ``PATH``.

Sin root no se toca nada: el comando escribe la unidad en ``<home>/inaki.service``
y muestra los tres ``sudo`` exactos. Con root (``sudo``) los ejecuta. Ese
diseño evita que ``sudo inaki`` tenga que ENCONTRAR ``inaki`` en el PATH de root,
que con pipx (``~/.local/bin``) no lo está.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import typer

from inaki.config.home import get_inaki_home

service_app = typer.Typer(help="Instalar o quitar la unidad systemd del daemon.")

NOMBRE_UNIDAD = "inaki.service"
RUTA_UNIDAD = Path("/etc/systemd/system") / NOMBRE_UNIDAD
ENLACE_CLI = Path("/usr/local/bin/inaki")

_PLANTILLA = """\
[Unit]
Description=Inaki — asistente personal agentico
Documentation=https://github.com/alberto2112/inaki
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={user}
Group={group}
WorkingDirectory={home}
Environment=INAKI_HOME={home}
ExecStart={exec_start} daemon
ExecReload=/bin/kill -HUP $MAINPID

# Reiniciar automáticamente si falla (salvo salida limpia o señal de stop)
Restart=on-failure
RestartSec=5s

# Logs van a journald (ver con: journalctl -u inaki -f)
StandardOutput=journal
StandardError=journal
SyslogIdentifier=inaki

# Límites de recursos para Raspberry Pi 5 (4GB RAM). Ajustar según uso real.
MemoryMax=2G
TasksMax=64

# Shutdown gracioso: SIGTERM, esperar 30s, luego SIGKILL
KillMode=process
KillSignal=SIGTERM
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
"""


def render_unit(*, user: str, group: str, exec_start: Path, home: Path) -> str:
    """La unidad como texto. Función pura: lo único que se testea del render."""
    return _PLANTILLA.format(user=user, group=group, exec_start=exec_start, home=home)


def ruta_del_cli() -> Path:
    """Ruta absoluta del ``inaki`` que está corriendo: el console script del entorno actual.

    Bajo ``python -m inaki`` ``argv[0]`` no es el script; ahí se cae a ``which``.
    """
    candidato = Path(sys.argv[0])
    if candidato.name == "inaki" and candidato.exists():
        return candidato.resolve()
    encontrado = shutil.which("inaki")
    if encontrado:
        return Path(encontrado).resolve()
    raise typer.BadParameter(
        "No encuentro el ejecutable `inaki` de este entorno. Corré el comando con el "
        "`inaki` instalado (venv o pipx), no con `python -m`."
    )


def _usuario_y_grupo() -> tuple[str, str]:
    """El usuario REAL (el que invocó ``sudo``), nunca root."""
    import grp
    import pwd

    user = os.environ.get("SUDO_USER") or pwd.getpwuid(os.getuid()).pw_name
    gid = pwd.getpwnam(user).pw_gid
    return user, grp.getgrgid(gid).gr_name


def _es_root() -> bool:
    return os.geteuid() == 0


def _systemctl(*args: str) -> None:
    subprocess.run(["systemctl", *args], check=True)


@service_app.command("install")
def install(
    link_cli: bool = typer.Option(
        False,
        "--link-cli",
        help="Además enlaza el CLI en /usr/local/bin/inaki. Ojo: eso lo deja visible para "
        "el shell_exec del agente (el PATH mínimo de systemd incluye /usr/local/bin).",
    ),
    print_only: bool = typer.Option(
        False, "--print", help="Solo muestra la unidad renderizada, sin escribir nada."
    ),
) -> None:
    """Genera la unidad systemd y, con sudo, la instala, habilita y arranca."""
    home = get_inaki_home().expanduser().resolve()
    user, group = _usuario_y_grupo()
    exec_start = ruta_del_cli()
    unidad = render_unit(user=user, group=group, exec_start=exec_start, home=home)

    if print_only:
        typer.echo(unidad, nl=False)
        return

    if not _es_root():
        borrador = home / NOMBRE_UNIDAD
        home.mkdir(parents=True, exist_ok=True)
        borrador.write_text(unidad, encoding="utf-8")
        typer.echo(f"Unidad generada en {borrador} (usuario {user}, ejecutable {exec_start}).")
        typer.echo("Para instalarla hacen falta permisos de root. Corré:\n")
        typer.echo(f"  sudo cp {borrador} {RUTA_UNIDAD}")
        typer.echo("  sudo systemctl daemon-reload")
        typer.echo("  sudo systemctl enable --now inaki")
        if link_cli:
            typer.echo(f"  sudo ln -sfn {exec_start} {ENLACE_CLI}")
        typer.echo("\nO repetí este comando con sudo y la ruta completa del ejecutable:")
        typer.echo(f"  sudo {exec_start} service install{' --link-cli' if link_cli else ''}")
        return

    RUTA_UNIDAD.write_text(unidad, encoding="utf-8")
    RUTA_UNIDAD.chmod(0o644)
    typer.echo(f"Unidad escrita en {RUTA_UNIDAD} (usuario {user}, ejecutable {exec_start}).")
    if link_cli:
        _enlazar_cli(exec_start)
    _systemctl("daemon-reload")
    _systemctl("enable", "inaki")
    _systemctl("restart", "inaki")
    typer.echo(
        "✓ Servicio instalado y arrancado.\n"
        "  systemctl status inaki     → estado\n"
        "  journalctl -u inaki -f     → logs en tiempo real"
    )


def _enlazar_cli(exec_start: Path) -> None:
    if ENLACE_CLI.exists() and not ENLACE_CLI.is_symlink():
        typer.echo(f"  ⚠ {ENLACE_CLI} ya existe y NO es un symlink — se omite para no pisarlo.")
        return
    ENLACE_CLI.parent.mkdir(parents=True, exist_ok=True)
    if ENLACE_CLI.is_symlink():
        ENLACE_CLI.unlink()
    ENLACE_CLI.symlink_to(exec_start)
    typer.echo(f"  ✓ {ENLACE_CLI} → {exec_start}")


@service_app.command("uninstall")
def uninstall() -> None:
    """Para y deshabilita el servicio, y borra la unidad (y el enlace del CLI si es nuestro)."""
    if not _es_root():
        typer.echo("Hacen falta permisos de root. Corré:\n")
        typer.echo("  sudo systemctl disable --now inaki")
        typer.echo(f"  sudo rm -f {RUTA_UNIDAD}")
        typer.echo("  sudo systemctl daemon-reload")
        if ENLACE_CLI.is_symlink():
            typer.echo(f"  sudo rm {ENLACE_CLI}")
        return
    if RUTA_UNIDAD.exists():
        _systemctl("disable", "--now", "inaki")
        RUTA_UNIDAD.unlink()
        _systemctl("daemon-reload")
        typer.echo(f"Unidad {RUTA_UNIDAD} eliminada.")
    else:
        typer.echo(f"No hay unidad en {RUTA_UNIDAD}; nada que quitar.")
    if ENLACE_CLI.is_symlink():
        ENLACE_CLI.unlink()
        typer.echo(f"Enlace {ENLACE_CLI} eliminado.")
