# Smoke Test Manual — `inaki setup` TUI V2 (Pi 5)

Checklist de verificación manual en Raspberry Pi 5 via SSH.
Correr esta lista en un entorno real después de cada release que toque el TUI de setup.

---

## Prerequisitos

- Iñaki instalado en el Pi 5 (modo systemd o venv directo).
- Al menos un agente configurado en `~/.inaki/config/agents/`.
- Conexión SSH activa con terminal de al menos 80x24.

---

## 1. Acceso desde SSH y pantalla de menú principal

```bash
ssh pi@raspi.local
cd ~/inaki
source .venv/bin/activate
inaki setup
```

**Esperado:** la TUI abre sin error y muestra `MainMenuPage` con 4 opciones:
`Global Config`, `Providers`, `Agentes`, `Secrets`.

**Si falla:** verificar `textual>=0.80` instalado (`pip show textual`). En Pi 5
con 4 GB RAM la TUI debe abrir en menos de 3 segundos.

- [ ] TUI abre sin traceback.
- [ ] Se muestra el menú principal con las 4 categorías.
- [ ] Breadcrumb en el top bar muestra `inaki / setup`.

---

## 2. Modal de bienvenida (primer lanzamiento)

Al abrir la TUI **por primera vez** (sin el flag `~/.inaki/setup_welcome_seen`):

- [ ] Se muestra el modal "inaki setup — TUI" con texto de bienvenida.
- [ ] El modal menciona `inaki setup secret-key` como alternativa para el wizard Fernet.
- [ ] Al presionar `Enter` o `Esc` el modal se cierra.
- [ ] En las aperturas siguientes **no** vuelve a aparecer.

Para resetear:

```bash
rm ~/.inaki/setup_welcome_seen
```

---

## 3. Navegación con teclado — teclado-first, sin mouse

Desde `MainMenuPage`:

- [ ] `↓` / `j` baja el cursor; la fila seleccionada se resalta con la barra teal `▎`.
- [ ] `↑` / `k` sube el cursor.
- [ ] `Enter` sobre "Global Config" abre `GlobalPage` (breadcrumb cambia a `inaki / config / global`).
- [ ] `Esc` en `GlobalPage` vuelve a `MainMenuPage`.
- [ ] `q` en cualquier pantalla sale limpiamente.

---

## 4. GlobalPage — editar un campo global

1. `Enter` sobre "Global Config" → `GlobalPage`.
2. Navegar con `↓` hasta cualquier campo de la sección `LLM` (ej. `model`).
3. Presionar `Enter` → se abre `EditScalarModal`.
4. El modal muestra el valor actual **pre-completado** en el input.
5. Cambiar el valor (ej. `anthropic/claude-3-5-haiku` → `anthropic/claude-3-haiku`).
6. Presionar `Enter` para guardar.

**Verificar en shell separado:**

```bash
bat ~/.inaki/config/global.yaml | rg "model"
```

- [ ] El modal pre-completa el valor actual.
- [ ] El valor nuevo aparece en `global.yaml`.
- [ ] Los **comentarios del archivo original están preservados** (ruamel.yaml).
- [ ] No hay errores de sintaxis YAML.
- [ ] Aparece notificación "guardado: model" en la barra de status.

---

## 5. GlobalPage — escape hatch `<null>`

1. En `GlobalPage`, navegar hasta un campo opcional (ej. `LLM → reasoning_effort`).
2. Presionar `Enter` → `EditScalarModal`.
3. Borrar el contenido e ingresar `<null>`.
4. Presionar `Enter`.

**Verificar:**

```bash
bat ~/.inaki/config/global.yaml | rg "reasoning_effort"
```

- [ ] El campo aparece como `reasoning_effort: null` en el YAML (no ausente, `null` explícito).

---

## 6. ProvidersPage — agregar un nuevo provider

1. En `MainMenuPage`, Enter sobre "Providers" → `ProvidersPage`.
2. Presionar `n` (nuevo provider) o el binding disponible.
3. Ingresar `id: test-provider`, `base_url: https://api.test.com/v1`.
4. Ingresar una `api_key` de prueba (ej. `sk-test-1234567890`).
5. Confirmar.

**Verificar:**

```bash
bat ~/.inaki/config/global.yaml | rg -A3 "test-provider"
bat ~/.inaki/config/global.secrets.yaml | rg "test-provider"
stat -f "%A" ~/.inaki/config/global.secrets.yaml   # debe ser 600
```

- [ ] `global.yaml` tiene la entrada `test-provider` con `base_url`.
- [ ] `global.secrets.yaml` tiene `api_key` del provider.
- [ ] Permisos de `global.secrets.yaml` son `600`.

---

## 7. AgentsPage — crear un nuevo agente

1. En `MainMenuPage`, Enter sobre "Agentes" → `AgentsPage`.
2. Presionar el binding de crear (ej. `n` o `c`).
3. Ingresar:
   - `id`: `smoke-test`
   - `name`: `Smoke Test Agent`
   - `description`: `Agente de prueba del TUI`
   - `system_prompt`: `Sos un agente de prueba.`
4. Confirmar.

**Verificar:**

```bash
bat ~/.inaki/config/agents/smoke-test.yaml
```

- [ ] El archivo existe con los 4 campos.
- [ ] Es YAML válido.

---

## 8. AgentDetailPage — flujo de edición con tri-estado (memory.llm)

1. En `AgentsPage`, seleccionar `smoke-test` → Enter → `AgentDetailPage`.
2. Breadcrumb muestra `inaki / config / agents / smoke-test`.
3. Navegar hasta la sección `MEMORY.LLM`.
4. Seleccionar el campo `provider`:
   - Presionar `Enter` → se abre el modal triestado con 3 opciones.
   - Seleccionar **Heredar** → guardar.
5. Seleccionar el campo `model`:
   - Presionar `Enter` → modal triestado.
   - Seleccionar **Valor propio** → ingresar `gpt-4o` → guardar.
6. Seleccionar el campo `temperature`:
   - Presionar `Enter` → modal triestado.
   - Seleccionar **null explícito** → guardar.

**Verificar:**

```bash
bat ~/.inaki/config/agents/smoke-test.yaml
```

- [ ] `memory.llm.provider` NO aparece en el YAML del agente (heredado).
- [ ] `memory.llm.model: gpt-4o` aparece explícitamente.
- [ ] `memory.llm.temperature: null` aparece explícitamente.

---

## 9. Validación cross-ref — warning por referencia inválida

1. En `GlobalPage`, navegar hasta `APP → default_agent`.
2. Presionar `Enter` → modal.
3. Ingresar un id inexistente (ej. `agente-fantasma`).
4. Presionar `Enter` para guardar.

**Esperado:**
- [ ] El valor se guarda (aparece en YAML).
- [ ] Aparece una notificación de **warning** indicando referencia inválida (`app.default_agent`).
- [ ] La TUI no se cierra ni rompe.
5. Corregir el valor volviendo al campo.

---

## 10. Preservación de comentarios YAML (ruamel.yaml)

1. Editar manualmente un comentario en `global.yaml`:

```bash
# Agregar un comentario antes de un campo, guardar
```

2. Abrir la TUI, editar cualquier campo en la misma sección, guardar.

**Verificar:**

```bash
bat ~/.inaki/config/global.yaml
```

- [ ] El comentario insertado manualmente sigue presente.
- [ ] Otros comentarios del archivo original no fueron eliminados.

---

## 11. `inaki setup secret-key` — wizard Fernet legacy

```bash
inaki setup secret-key
```

- [ ] Se abre el wizard interactivo.
- [ ] No hay traceback ni error de importación.

---

## 12. `inaki setup webui` — placeholder

```bash
inaki setup webui
```

- [ ] Imprime: `Próximamente — usá \`inaki setup tui\` por ahora.`
- [ ] Sale con código 0 (`echo $?` → `0`).

---

## 13. Performance en Pi 5 (4 GB RAM)

- [ ] La TUI abre en ≤ 3 segundos desde el comando hasta primer render.
- [ ] Navegar entre secciones de `GlobalPage` responde sin lag visible.
- [ ] Guardar un campo no congela la UI más de 1 segundo.

Si hay sluggishness notable, documentar:
- qué operación fue lenta
- cuánto tardó aproximadamente
- si mejora usando `PYTHONOPTIMIZE=1`

---

## 14. Cleanup post-smoke

```bash
rm -f ~/.inaki/config/agents/smoke-test.yaml
# Restaurar global.yaml si fue modificado
# Para volver a ver el modal de bienvenida:
rm -f ~/.inaki/setup_welcome_seen
```

---

## Criterio de aceptación

Todos los ítems marcados ✅. Si alguno falla, crear issue con:
- output de la terminal (o screenshot del TUI)
- versión de Python (`python --version`)
- versión de Textual (`pip show textual`)
- modelo de Pi y cantidad de RAM
