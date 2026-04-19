# Flujo Canal → LLM — Iñaki v2

Cómo viaja un mensaje desde que el usuario lo envía hasta que el LLM responde.
Incluye el flujo sin tools y el flujo con tool calls.

---

## 1. Entrada por canal

### CLI

```
Usuario escribe en terminal
    ↓
cli_runner.run_cli()
    ↓
user_input = input("tú > ").strip()
    ↓
container.run_agent.execute(user_input)
```

### Telegram

```
Usuario envía mensaje al bot
    ↓
python-telegram-bot: Update llega al handler
    ↓
TelegramBot._handle_message(update, context)
    ↓
telegram_update_to_input(update) → str
    ↓
container.run_agent.execute(user_input)
    ↓ [respuesta]
update.message.reply_text(response)
```

### REST API

```
App Android: POST /chat {"message": "..."}  con header X-API-Key
    ↓
FastAPI verifica X-API-Key → 401 si inválida
    ↓
routers/agents.py: chat(body, container)
    ↓
container.run_agent.execute(body.message)
    ↓ [respuesta]
return ChatResponse(agent_id, agent_name, response)
```

<!-- SSE streaming endpoint eliminado en el cambio cli-chat-via-rest. Si en el futuro se requiere streaming UX, debe diseñarse pasando por RunAgentUseCase.execute(). -->

---

## 2. RunAgentUseCase.execute() — flujo sin tools

```
RunAgentUseCase.execute(user_input)
│
├── 1. HISTORIAL
│   └── history.load(agent_id) → list[Message]
│       SQLiteHistoryStore: SELECT ... WHERE agent_id=? AND archived=0 ORDER BY id DESC LIMIT N
│       Resultado: [Message(USER, "...", timestamp=...), Message(ASSISTANT, "...", timestamp=...), ...]
│
├── 2. EMBEDDING DEL INPUT
│   └── embedder.embed_query(user_input) → list[float] (384d)
│       E5OnnxProvider: añade prefijo "query: " internamente
│       Resultado: vector normalizado L2
│
├── 3. BÚSQUEDA EN MEMORIA
│   └── memory.search(query_vec, top_k=5) → list[MemoryEntry]
│       SQLiteMemoryRepository:
│         query_vec → bytes (struct.pack)
│         SELECT ... FROM memory_embeddings WHERE embedding MATCH ? AND k = ?
│         JOIN memories ON id
│         ORDER BY distance (cosine KNN)
│
├── 4. SEMANTIC ROUTING — SKILLS
│   └── skills.retrieve(query_vec, top_k=3) → list[Skill]
│       YamlSkillRepository:
│         cosine_similarity(query_vec, skill_embedding) para cada skill
│         sort descending → top 3
│
├── 5. CONSTRUIR CONTEXT Y SYSTEM PROMPT
│   └── AgentContext(agent_id, memories, skills)
│       .build_system_prompt(base_prompt) →
│         "{system_prompt del agente}
│
│          ## Lo que recuerdas del usuario:
│          - Le gusta Python
│          - Prefiere respuestas concisas
│
│          ## Skills disponibles:
│          - **Búsqueda Web**: Busca información actualizada..."
│
├── 6. LLAMAR AL LLM
│   └── llm.complete(messages=[*history, user_msg], system_prompt, tools=None)
│       OpenRouterProvider:
│         POST https://openrouter.ai/api/v1/chat/completions
│         {
│           "model": "anthropic/claude-3-5-haiku",
│           "messages": [
│             {"role": "system", "content": "...system prompt..."},
│             {"role": "user", "content": "mensaje previo"},
│             {"role": "assistant", "content": "respuesta previa"},
│             {"role": "user", "content": "input actual"}
│           ],
│           "temperature": 0.7,
│           "max_tokens": 2048
│         }
│       ← response: {"choices": [{"message": {"content": "Respuesta del LLM"}}]}
│       → return "Respuesta del LLM"
│
├── 7. PERSISTIR EN HISTORIAL
│   ├── history.append(agent_id, Message(USER, user_input))
│   └── history.append(agent_id, Message(ASSISTANT, response))
│       SQLiteHistoryStore: INSERT INTO history (agent_id, role, content, created_at)
│       message.timestamp se muta en place con datetime.now(UTC) si era None
│
└── 8. RETURN response → canal de origen
```

---

## 3. RunAgentUseCase.execute() — flujo CON tools

Las tools se activan cuando el LLM decide usarlas. El flujo es idéntico hasta el paso 6,
donde la respuesta contiene `tool_calls` en lugar de texto.

```
[pasos 1-5 iguales al flujo sin tools]
│
├── 6. LLAMAR AL LLM CON SCHEMAS DE TOOLS
│   └── llm.complete(messages, system_prompt, tools=[
│         {
│           "type": "function",
│           "function": {
│             "name": "web_search",
│             "description": "Busca información en internet...",
│             "parameters": {"type": "object", "properties": {"query": {...}}}
│           }
│         },
│         {
│           "type": "function",
│           "function": {
│             "name": "shell_exec",
│             "description": "Ejecuta un comando shell...",
│             ...
│           }
│         }
│       ])
│       ← response: '{"tool_calls": [{"function": {"name": "web_search", "arguments": "{\"query\": \"Python 3.13 features\"}"}}]}'
│
├── 7. DETECTAR TOOL CALLS (_extract_tool_calls)
│   └── json.loads(raw) → {"tool_calls": [...]}
│       → list of tool_calls [{function: {name, arguments}}]
│
├── 8. LOOP DE EJECUCIÓN DE TOOLS (máx 5 iteraciones)
│   │
│   ├── Para cada tool_call:
│   │   ├── tool_name = tc["function"]["name"]           → "web_search"
│   │   ├── kwargs = json.loads(tc["function"]["arguments"]) → {"query": "Python 3.13"}
│   │   └── tool_result = await tool_executor.execute(tool_name, **kwargs)
│   │       WebSearchTool.execute(query="Python 3.13 features")
│   │       ← ToolResult(tool_name="web_search", output="1. Python 3.13...\n...", success=True)
│   │
│   ├── Construir resumen de resultados:
│   │   "[web_search]: 1. Python 3.13 released...\n   https://...\n   ..."
│   │
│   ├── Añadir a working_messages:
│   │   Message(role=USER, content="[Resultados de tools]\n[web_search]: ...")
│   │
│   └── RELLAMAR AL LLM con los resultados
│       llm.complete(working_messages + tool_results, system_prompt, tools=schemas)
│       ← Si respuesta es texto → salir del loop
│       ← Si hay más tool_calls → iterar (máx 5 veces)
│
├── 9. PERSISTIR EN HISTORIAL
│   ├── history.append(agent_id, Message(USER, user_input))    ← solo el input original
│   └── history.append(agent_id, Message(ASSISTANT, response)) ← solo la respuesta final
│       SQLiteHistoryStore: INSERT INTO history (agent_id, role, content, created_at)
│       ⚠ Los mensajes de tool calls y tool results NO se persisten en el historial
│
└── 10. RETURN response final → canal de origen
```

---

## 4. Flujo de consolidación (`/consolidate`)

```
usuario: /consolidate
    ↓
cli_runner (o TelegramBot._cmd_consolidate)
    ↓
container.consolidate_memory.execute()
│
├── 1. history.load_full(agent_id) → list[Message]
│   Si vacío → return "El historial está vacío — nada que consolidar."
│   SQLiteHistoryStore: SELECT ... WHERE agent_id=? AND archived=0 ORDER BY id ASC
│
├── 2. Formatear historial para el LLM (con timestamps si están presentes):
│   "user [2026-04-09T15:30:00Z]: hola\nassistant [2026-04-09T15:30:45Z]: hola también\n..."
│   Si timestamp=None: "user: me gusta Python\n..."
│
├── 3. llm.complete(messages=[], system_prompt=EXTRACTOR_PROMPT)
│   ← '[{"content": "Le gusta Python", "relevance": 0.9, "tags": ["tech"], "timestamp": "2026-04-09T15:30:00Z"}, ...]'
│   Si falla → ConsolidationError, historial INTACTO, FIN
│
├── 4. parse JSON → list[{content, relevance, tags, timestamp?}]
│   Si JSON inválido → ConsolidationError, historial INTACTO, FIN
│
├── 5. Para cada recuerdo:
│   ├── embedder.embed_passage(content) → vector 384d
│   │   E5OnnxProvider: añade prefijo "passage: " internamente
│   ├── created_at = datetime.fromisoformat(fact["timestamp"]) si existe, else datetime.now(UTC)
│   ├── MemoryEntry(id=UUID, content, embedding, relevance, tags, created_at, agent_id=None)
│   └── memory.store(entry)
│       SQLiteMemoryRepository:
│         INSERT INTO memories (...)
│         INSERT INTO memory_embeddings (id, embedding_bytes)
│   Si store falla → ConsolidationError, historial INTACTO, FIN
│
├── 6. SOLO SI TODO OK:
│   ├── history.archive(agent_id)
│   │   SQLiteHistoryStore: UPDATE history SET archived=1 WHERE agent_id=? AND archived=0
│   └── history.clear(agent_id)
│       SQLiteHistoryStore: DELETE FROM history WHERE agent_id=?
│
└── return "✓ 3 recuerdo(s) extraído(s). Historial archivado (Historial de 'general' archivado.)."
```

---

## 5. Diagrama de flujo completo (texto)

```
                    CANAL
          ┌──────────────────────┐
          │  CLI / Telegram / REST│
          └──────────┬───────────┘
                     │ user_input: str
                     ▼
          ┌──────────────────────┐
          │   RunAgentUseCase    │
          │                      │
          │  embed_query(input)  │◄── IEmbeddingProvider (E5 ONNX)
          │         │            │
          │  memory.search()  ───┤◄── IMemoryRepository (SQLite + vec0)
          │  skills.retrieve()───┤◄── ISkillRepository (YAML + cosine sim)
          │         │            │
          │  history.load()   ───┤◄── IHistoryStore (SQLite — data/history.db)
          │         │            │
          │  build_system_prompt │◄── AgentContext
          │         │            │
          │  llm.complete()   ───┤◄── ILLMProvider (OpenRouter/Ollama/...)
          │         │            │
          │  ┌─ tool_calls? ─┐  │
          │  │ YES            │NO│
          │  │ execute tools  │  │
          │  │ re-call LLM    │  │
          │  └────────────────┘  │
          │         │            │
          │  history.append()    │◄── IHistoryStore
          └──────────┬───────────┘
                     │ response: str
                     ▼
          ┌──────────────────────┐
          │  CANAL (responde)     │
          │  CLI / Telegram / REST│
          └──────────────────────┘
```
