#!/bin/bash
# Instala y habilita el servicio systemd de Iñaki.
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

if [ ! -f "$SERVICE_TEMPLATE" ]; then
    echo "Error: $SERVICE_TEMPLATE no encontrado."
    exit 1
fi

if [ ! -x "$VENV_PYTHON" ]; then
    echo "Error: no existe $VENV_PYTHON. Creá el venv e instalá deps antes:"
    echo "  python3 -m venv .venv && .venv/bin/pip install -e ."
    exit 1
fi

echo "Instalando Iñaki:"
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
