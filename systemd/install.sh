#!/bin/bash
# Instala y habilita el servicio systemd de Inaki.
# Ejecutar con sudo desde el directorio raíz del repo:
#   sudo bash systemd/install.sh

set -e

if [ "$EUID" -ne 0 ]; then
    echo "Error: este script debe ejecutarse con sudo."
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INAKI_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVICE_TEMPLATE="$SCRIPT_DIR/inaki.service"
SYSTEMD_DIR="/etc/systemd/system"
SERVICE_TARGET="$SYSTEMD_DIR/inaki.service"

# Usuario real (el que invocó sudo), no root
RUN_USER="${SUDO_USER:-$USER}"
RUN_GROUP="$(id -gn "$RUN_USER")"

VENV_PYTHON="$INAKI_DIR/.venv/bin/python"

# CLI del venv → /usr/local/bin. Es el ÚNICO directorio que cumple las dos
# condiciones: el FHS lo reserva para software local (a diferencia de /usr/bin,
# territorio de dpkg/apt) y está en el PATH mínimo de systemd — que NO lee
# .bashrc/.profile. Sin este enlace, `inaki` solo existe dentro del venv: ni tu
# shell ni el daemon (ni por ende el `shell_exec` del agente) lo encuentran.
VENV_CLI="$INAKI_DIR/.venv/bin/inaki"
CLI_LINK="/usr/local/bin/inaki"

if [ ! -f "$SERVICE_TEMPLATE" ]; then
    echo "Error: $SERVICE_TEMPLATE no encontrado."
    exit 1
fi

if [ ! -x "$VENV_PYTHON" ]; then
    echo "Error: no existe $VENV_PYTHON. Creá el venv e instalá deps antes:"
    echo "  python3 -m venv .venv && .venv/bin/pip install -e ."
    exit 1
fi

echo "Instalando Inaki:"
echo "  Repo:    $INAKI_DIR"
echo "  Usuario: $RUN_USER ($RUN_GROUP)"
echo "  Python:  $VENV_PYTHON"
echo ""

# Genera el service file con valores reales
echo "Generando $SERVICE_TARGET..."
sed \
    -e "s|^User=.*|User=$RUN_USER|" \
    -e "s|^Group=.*|Group=$RUN_GROUP|" \
    -e "s|^WorkingDirectory=.*|WorkingDirectory=$INAKI_DIR|" \
    -e "s|^ExecStart=.*|ExecStart=$VENV_PYTHON main.py daemon|" \
    "$SERVICE_TEMPLATE" > "$SERVICE_TARGET"

chmod 644 "$SERVICE_TARGET"

# Enlaza el CLI. El console script de pip lleva shebang con ruta ABSOLUTA al
# python del venv, así que el symlink resuelve al intérprete correcto sin wrapper.
echo "Enlazando el CLI en $CLI_LINK..."
if [ ! -x "$VENV_CLI" ]; then
    echo "  ⚠ No existe $VENV_CLI — se omite el enlace."
    echo "    Instalá el paquete en el venv: .venv/bin/pip install -e ."
elif [ -e "$CLI_LINK" ] && [ ! -L "$CLI_LINK" ]; then
    # Fichero real (no un symlink nuestro): no lo pisamos a ciegas.
    echo "  ⚠ $CLI_LINK ya existe y NO es un symlink — se omite para no pisarlo."
    echo "    Revisalo a mano si querés que apunte a $VENV_CLI."
else
    mkdir -p "$(dirname "$CLI_LINK")"
    ln -sfn "$VENV_CLI" "$CLI_LINK"
    echo "  ✓ $CLI_LINK → $VENV_CLI"
fi

echo "Recargando systemd..."
systemctl daemon-reload

echo "Habilitando servicio (arranque automático al boot)..."
systemctl enable inaki

echo "Iniciando servicio..."
systemctl restart inaki

echo ""
echo "✓ Servicio instalado y arrancado."
echo ""
echo "Comandos útiles:"
echo "  systemctl status inaki       → estado del servicio"
echo "  journalctl -u inaki -f       → logs en tiempo real"
echo "  systemctl stop inaki         → detener"
echo "  systemctl restart inaki      → reiniciar"
echo "  systemctl disable inaki      → deshabilitar arranque automático"
echo "  inaki --version              → CLI (ya disponible sin activar el venv)"
echo ""
echo "Nota: el enlace en $CLI_LINK también hace visible el CLI para el"
echo "      \`shell_exec\` del agente. Si no lo querés, borralo:"
echo "      sudo rm $CLI_LINK"
