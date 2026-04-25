# CHANGELOG

## Unreleased

### setup-tui-config

- `inaki setup` y `inaki setup tui` ahora abren la **TUI Textual** para editar
  `~/.inaki/config/*.yaml` con awareness de las 4 capas de configuración.
- El wizard Fernet anterior pasó a `inaki setup secret-key`.
  Si usabas `inaki setup` para configurar la clave de encriptación, ejecutá
  `inaki setup secret-key` en su lugar.
- `inaki setup webui` imprime un placeholder ("no implementado todavía") y sale.
- Nuevas dependencias: `textual>=0.80`, `ruamel.yaml>=0.18`.
- Nota: los cambios guardados desde la TUI toman efecto al próximo reinicio
  del daemon (`inaki daemon` o `systemctl restart inaki`).
