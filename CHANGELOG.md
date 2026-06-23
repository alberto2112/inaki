# CHANGELOG

## Unreleased

### setup-tui: descripciones de campo + listas dinámicas

- El setup TUI muestra la **descripción de cada campo** (docstring del schema vía
  `use_attribute_docstrings`) como ayuda al añadir un campo o sección — antes
  había que leer el código fuente.
- Los campos con **valores conocidos** se editan como **lista** en vez de texto
  libre: `llm/embedding/transcription.provider` ofrecen los providers declarados
  en `providers:`; `memories.{consolidation,reconciliation}.agent_id` ofrecen los
  sub-agentes declarados. Mapeo por ruta; respeta los `Literal` del schema
  (`photos.scene.provider` conserva sus opciones).

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
