#!/bin/bash
# Instala y habilita el servicio systemd de Iñaki.
# Ejecutar como root: sudo bash systemd/install.sh

set -e

INAKI_DIR="/home/pi/inaki"
SERVICE_FILE="$INAKI_DIR/systemd/inaki.service"
SYSTEMD_DIR="/etc/systemd/system"

if [ ! -f "$SERVICE_FILE" ]; then
    echo "Error: $SERVICE_FILE no encontrado. Ejecutar desde el directorio raíz de Iñaki."
    exit 1
fi

echo "Copiando inaki.service a $SYSTEMD_DIR..."
cp "$SERVICE_FILE" "$SYSTEMD_DIR/inaki.service"

echo "Recargando systemd..."
systemctl daemon-reload

echo "Habilitando servicio (arranque automático al boot)..."
systemctl enable inaki

echo "Iniciando servicio..."
systemctl start inaki

echo ""
echo "✓ Servicio instalado y arrancado."
echo ""
echo "Comandos útiles:"
echo "  systemctl status inaki       → estado del servicio"
echo "  journalctl -u inaki -f       → logs en tiempo real"
echo "  systemctl stop inaki         → detener"
echo "  systemctl restart inaki      → reiniciar"
echo "  systemctl disable inaki      → deshabilitar arranque automático"
