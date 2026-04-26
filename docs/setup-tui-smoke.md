# Smoke Test Manual — `inaki setup` TUI (Pi 5)

Checklist de verificación manual en Raspberry Pi 5 via SSH.
Correr esta lista en un entorno real después de cada release que toque el TUI de setup.

---

## Prerequisitos

- Iñaki instalado en el Pi 5 (modo systemd o venv directo).
- Al menos un agente configurado en `~/.inaki/config/agents/`.
- Conexión SSH activa con terminal de al menos 80x24.

---

## 1. Acceso desde SSH

```bash
ssh pi@raspi.local
cd ~/inaki
source .venv/bin/activate   # o el venv que uses
inaki setup
```

**Esperado:** la TUI abre sin error, muestra el header "inaki setup" y un menú de secciones (GlobalScreen).

**Si falla:** verificar que `textual>=0.80` está instalado (`pip show textual`). En Pi 5 con 4 GB RAM la TUI debe abrir en menos de 3 segundos.

---

## 2. Modal de bienvenida (primer lanzamiento)

Al abrir la TUI **por primera vez** (sin el flag `~/.inaki/setup_welcome_seen`):

- [ ] Se muestra el modal "inaki setup — TUI" con texto de bienvenida.
- [ ] El modal menciona que el wizard Fernet ahora está en `inaki setup secret-key`.
- [ ] Al presionar Enter o hacer click en "Entendido" el modal se cierra.
- [ ] En las aperturas siguientes **no** vuelve a aparecer (flag ya escrito).

Para resetear:

```bash
rm ~/.inaki/setup_welcome_seen
```

---

## 3. Navegación entre pantallas principales

Teclas: `1` Global · `2` Providers · `3` Agentes · `4` Secrets · `Q` salir.

- [ ] `1` → GlobalScreen muestra un menú con las secciones (app, llm, embedding, memory, etc.).
- [ ] `2` → ProvidersScreen lista los providers del global.yaml sin mostrar api_key.
- [ ] `3` → AgentsScreen lista los agentes en `~/.inaki/config/agents/`.
- [ ] `4` → SecretsScreen muestra campos de `*.secrets.yaml` con bullets.
- [ ] `Q` → sale limpiamente sin escribir nada.

---

## 4. Editar campo global (sección `llm`) y guardar

Flujo completo de edición con diff preview y confirmación:

1. Desde GlobalScreen presionar `1` (o el número de la fila `llm`).
2. En LLMScreen, cambiar el campo `model` a un valor distinto (ej: `anthropic/claude-3-haiku`).
3. Presionar `Ctrl+S`.
4. **Esperado:** aparece un diff preview que muestra el cambio (`-` línea vieja, `+` línea nueva).
5. Confirmar con `Y` o el botón "Guardar".
6. **Verificar en shell separado:**

```bash
bat ~/.inaki/config/global.yaml | rg "model"
```

- [ ] El valor nuevo aparece en `global.yaml`.
- [ ] Los comentarios del archivo original están preservados (sin eliminar ni corromper).
- [ ] El archivo no tiene errores de sintaxis YAML (`python -c "import yaml; yaml.safe_load(open('/root/.inaki/config/global.yaml'))"` no lanza excepción).

---

## 5. Pantalla Providers — agregar un nuevo provider

1. Abrir ProvidersScreen (`2`).
2. Presionar `A` (agregar) o el botón "Nuevo provider".
3. Ingresar `id: test-provider`, `base_url: https://api.test.com/v1`.
4. Ingresar una `api_key` de prueba (ej: `sk-test-1234567890`).
5. Confirmar.
6. **Verificar:**

```bash
bat ~/.inaki/config/global.yaml | rg -A3 "test-provider"
bat ~/.inaki/config/global.secrets.yaml | rg "test-provider"
stat -c "%a" ~/.inaki/config/global.secrets.yaml   # debe ser 600
```

- [ ] `global.yaml` tiene la entrada `test-provider` con `base_url`.
- [ ] `global.secrets.yaml` tiene `providers.test-provider.api_key` con el valor ingresado.
- [ ] Permisos de `global.secrets.yaml` son `600`.

---

## 6. Pantalla Agentes — crear agente nuevo

1. Abrir AgentsScreen (`3`).
2. Presionar `C` (crear nuevo agente).
3. Ingresar:
   - `id`: `smoke-test`
   - `name`: `Smoke Test Agent`
   - `description`: `Agente de prueba del TUI`
   - `system_prompt`: `Sos un agente de prueba.`
4. Confirmar.
5. **Verificar:**

```bash
bat ~/.inaki/config/agents/smoke-test.yaml
```

- [ ] El archivo `~/.inaki/config/agents/smoke-test.yaml` existe.
- [ ] Tiene los 4 campos ingresados.
- [ ] Es YAML válido.

---

## 7. Editar agente — sección `memory.llm` con TristateToggle

1. En AgentsScreen, seleccionar el agente `smoke-test` → abrir editor.
2. Navegar a la sección `memory.llm`.
3. En AgentMemoryLLMScreen, probar los tres estados para **un campo diferente** cada uno:

   | Campo | Estado objetivo | YAML esperado |
   |-------|----------------|---------------|
   | `provider` | **Heredar** | campo ausente en el YAML del agente |
   | `model` | **Valor propio** (ej: `gpt-4o`) | `memory.llm.model: gpt-4o` |
   | `temperature` | **Override null** | `memory.llm.temperature: null` |

4. Guardar con `Ctrl+S`.
5. **Verificar:**

```bash
bat ~/.inaki/config/agents/smoke-test.yaml
```

- [ ] `memory.llm.provider` NO aparece en el YAML del agente (heredado).
- [ ] `memory.llm.model: gpt-4o` aparece explícitamente.
- [ ] `memory.llm.temperature: null` aparece explícitamente (null en YAML, no ausente).

---

## 8. `inaki setup secret-key` — wizard Fernet legacy

En una terminal separada (sin TUI abierta):

```bash
inaki setup secret-key
```

- [ ] Se abre el wizard interactivo de `setup_wizard.py`.
- [ ] Pregunta sobre `INAKI_SECRET_KEY`.
- [ ] No hay traceback ni error de importación.

---

## 9. `inaki setup webui` — placeholder

```bash
inaki setup webui
```

- [ ] Imprime: `Próximamente — usá \`inaki setup tui\` por ahora.`
- [ ] Sale con código 0 (`echo $?` → `0`).

---

## 10. Performance en Pi 5 (4 GB RAM)

- [ ] La TUI abre en ≤ 3 segundos desde el comando hasta primer render.
- [ ] Navegación entre secciones (teclas 1-4) responde sin lag visible.
- [ ] Guardar un campo no congela la UI más de 1 segundo.

Si hay sluggishness notable, documentar:
- qué operación fue lenta
- cuánto tardó aproximadamente
- si mejora usando `PYTHONOPTIMIZE=1`

---

## 11. Cleanup post-smoke

```bash
rm -f ~/.inaki/config/agents/smoke-test.yaml
# Si querés volver a ver el modal de bienvenida:
rm -f ~/.inaki/setup_welcome_seen
```

---

## Criterio de aceptación

Todos los ítems marcados ✅. Si alguno falla, crear issue con:
- output de la terminal (o screenshot del TUI)
- versión de Python (`python --version`)
- versión de Textual (`pip show textual`)
- modelo de Pi y cantidad de RAM (`cat /proc/cpuinfo | rg Model`, `free -h`)
