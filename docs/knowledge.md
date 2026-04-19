# Knowledge — Cómo darle conocimiento a Iñaki

Este documento explica cómo añadir fuentes de conocimiento externas al agente. Para la referencia completa de parámetros YAML ver `docs/configuracion.md`.

---

## Conceptos clave

Iñaki tiene dos formas de "recordar" cosas:

| Mecanismo | Qué es | Cuándo se usa |
|-----------|--------|---------------|
| **Memoria** | Hechos del usuario aprendidos durante conversaciones | Automático, siempre activo |
| **Knowledge** | Documentos o bases de datos que el usuario provee | Requiere configuración explícita |

Las fuentes de knowledge se consultan en cada turno (pre-fetch automático) y también están disponibles vía la tool `knowledge_search` para búsquedas explícitas.

---

## Caso 1 — Tengo una carpeta con documentos

El caso más común: tenés archivos `.md`, `.txt` o `.pdf` y querés que Iñaki los entienda.

**Paso 1 — Configurar la fuente en `~/.inaki/config/global.yaml`:**

```yaml
knowledge:
  sources:
    - id: "mis-docs"
      type: document
      path: ~/documentos/proyecto/
      glob: "**/*.md"
```

**Paso 2 — Indexar:**

```bash
inaki knowledge index mis-docs
```

Salida esperada:
```
Indexing source 'mis-docs'...
  Indexed 12 files, 48 chunks
Done.
```

**Paso 3 — Verificar:**

```bash
inaki knowledge list          # muestra todas las fuentes y su estado
inaki knowledge stats mis-docs  # archivos, chunks, última indexación, dimensión
```

A partir de aquí, en cada conversación Iñaki recupera los fragmentos más relevantes para la pregunta actual y los inyecta en el contexto antes de responder.

### Formatos soportados

| Formato | Estrategia de chunking |
|---------|------------------------|
| `.md`   | Split por headers (`#`/`##`/`###`), ventana deslizante dentro de cada sección |
| `.txt`  | Ventana deslizante pura |
| `.pdf`  | Extracción página a página, ventana deslizante sobre el texto total |
| otros   | Ventana deslizante pura |

### Actualizar el índice

La indexación es **incremental**: solo re-procesa archivos cuya `mtime` cambió desde la última vez. Para añadir o actualizar un documento, alcanza con:

1. Copiar o modificar el archivo en la carpeta configurada
2. Volver a correr `inaki knowledge index <id>`

El índice se guarda en `~/.inaki/knowledge/<id>.db` — no en el proyecto.

---

## Caso 2 — Tengo una base de datos SQLite propia

Si ya tenés embeddings calculados en SQLite (por ejemplo, generados con otro pipeline), podés conectarla directamente sin que Iñaki la re-indexe.

```yaml
knowledge:
  sources:
    - id: "mi-base"
      type: sqlite
      path: ~/data/knowledge.db
```

Iñaki **no escribe** esta DB — solo la consulta. La DB debe tener el schema que Iñaki espera (tabla `chunks` + tabla virtual `chunk_embeddings` con vectores de 384 dimensiones). Ver `docs/configuracion.md` para el schema exacto y un ejemplo de inserción.

**Requisito crítico**: los embeddings deben ser de 384 dimensiones (e5-small ONNX o texto equivalente). Si la DB usa otra dimensión, la fuente falla al arrancar con un error claro en los logs.

---

## Caso 3 — Fuente personalizada vía extensión

Si ninguno de los dos tipos anteriores sirve (por ejemplo, querés consultar una API externa, una DB PostgreSQL, o un índice Elasticsearch), podés implementar tu propia fuente en `ext/`:

```python
# ext/mi_extension/manifest.py

def _build_mi_fuente(agent_config, global_config, embedder):
    from mi_extension.fuente import MiFuente
    return MiFuente(embedder=embedder)

KNOWLEDGE_SOURCES = [_build_mi_fuente]
```

La factory recibe `(agent_config, global_config, embedder)` y debe retornar un objeto que implemente `IKnowledgeSource` (`core/ports/outbound/knowledge_port.py`). Si la factory lanza una excepción, se loguea como WARNING y el resto de fuentes sigue funcionando.

El orden de registro garantizado es: **(1) memoria** → **(2) fuentes en config** → **(3) fuentes de extensiones**.

---

## ¿Puedo enviarle un documento directamente en el chat?

No existe ese mecanismo hoy. No podés pegar un `.md` en el chat y que quede indexado. El flujo siempre es:

```
Copiar archivo a la carpeta → inaki knowledge index <id> → consulta activa
```

Una tool `knowledge_add_document` que automatice esto sería una extensión natural del pipeline actual pero no está implementada.

---

## Control del pre-fetch

Por defecto Iñaki hace un pre-fetch automático en cada turno. Podés ajustarlo:

```yaml
knowledge:
  enabled: false          # Deshabilita el pre-fetch automático.
                          # Las fuentes siguen disponibles vía knowledge_search.
  top_k_per_source: 3     # Fragmentos máximos por fuente.
  min_score: 0.5          # Score mínimo de similitud (0.0 – 1.0).
  max_total_chunks: 10    # Cap total de fragmentos tras el fan-out.
```

Si `enabled: false`, el pre-fetch se saltea pero el usuario puede seguir invocando `knowledge_search` explícitamente para buscar en las fuentes.

---

## Archivos de referencia

| Rol | Archivo |
|-----|---------|
| Referencia YAML completa | `docs/configuracion.md` — sección `knowledge:` |
| Puerto `IKnowledgeSource` | `core/ports/outbound/knowledge_port.py` |
| Adaptador para documentos | `adapters/outbound/knowledge/document_knowledge_source.py` |
| Adaptador para SQLite | `adapters/outbound/knowledge/sqlite_knowledge_source.py` |
| Tool de búsqueda explícita | `adapters/outbound/tools/knowledge_search_tool.py` |
| CLI de gestión | `adapters/inbound/cli/knowledge_cli.py` |
