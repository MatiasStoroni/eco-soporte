# AGENTS.md — demo ECO360 (chatbot RAG B2B + panel de administración)

Contexto operativo para agentes de IA. Leelo entero antes de tocar el repo: está ordenado de lo más crítico
(reglas que no se rompen) a lo más específico (referencia). Verificado contra el código el 2026-10-06.
Si algo de acá contradice al código, **manda el código**: actualizá este archivo en el mismo cambio.

- **Idioma con el usuario**: español rioplatense, claro y directo (voseo).
- **Docs complementarias**: `ECO-KB/AGENTE-DEBUG.md` (playbook detallado de depuración de respuestas),
  `ECO-KB/README.md` (panel y derivación), `README.md` (despliegue y reset), `ECO-KB/docs/estructurados/LEEME -
  Pendientes y dudas.md` (huecos de contenido de la KB).

---

## 0. TL;DR

| Qué | Valor |
|---|---|
| Producto | Chatbot de higiene profesional ECO360 (marca SecondSkin, `secondskin.com.ar`). Productos: OZONIFY, X3, X4, X5, SURFACE PROTECTANT, BIO SANITIZER, Blue Test, Carro Ozonify Industrial, OZONIFY PRO |
| Propósito | Demo para el equipo interno: simple de levantar, que "se vea profesional" |
| Repo | GitHub `MatiasStoroni/eco-soporte`, rama `master`. Local: `C:\Users\Follow\demo-eco` (Windows) |
| Backend | `ECO-KB/`: Python 3.12, `uv`, FastAPI, LangGraph, Gemini, Postgres 18 + pgvector |
| Frontend | `chat-view-test/`: Vite 6, JS vanilla (sin framework). Dos páginas: chat (`index.html`) y panel (`admin.html`) |
| Flujos | `support` (`is_registered=true`, documentación técnica + fuentes) y `sales` (`is_registered=false`, comercial + CTA) |
| Tipos de cliente | `hotel`, `bodega`, `restaurante`, `generic` |
| Modelos | Generar: `google_genai:gemini-3.5-flash` (thinking `low`). Clasificar/reescribir/evaluar: `google_genai:gemini-3.5-flash-lite`. Embeddings: `gemini-embedding-001` a 768 dims |
| Panel admin | `/admin` en la vista, contraseña `admin` (env `ADMIN_TOKEN`). Conversaciones + **base de conocimiento** (los operadores suben, revisan y publican los archivos ahí) |
| Servidor | `/proyectos/eco-soporte-dev` (usuario `dev-user`). Vista en `:8088`, API en `127.0.0.1:8000` |
| Tests | 126 unit/e2e sin red (`pytest`); 37 de integración (`-m integration`, **destructivos**, ver §9) |
| Evals | `evals/questions.yaml` + `evals/run.py`, con LLM real (gasta cuota). Línea base 2026-10-05: 100 % (342/342 = 114 casos × 3) |

---

## 1. Reglas que no se rompen (invariantes)

Si una tarea parece requerir romper una de estas reglas, **frená y preguntá al usuario**.

1. **El flujo lo decide solo `is_registered`**, nunca el texto del mensaje. `is_registered`, `client_type`,
   `flow` y `retrieval_filter` (`PROTECTED_FIELDS` en `graph/state.py`) los fija **únicamente**
   `validate_session`. Todos los demás nodos están envueltos en `guarded()`, que lanza `ProtectedFieldError` si
   intentan modificarlos.
2. **Aislamiento soporte/ventas y por tipo de cliente, en cuatro capas**:
   - tabla `kb_chunks` particionada por `audience`;
   - cada flujo usa su propio rol de solo lectura (`rag_support_ro` / `rag_sales_ro`), que solo tiene `SELECT`
     sobre su partición;
   - **RLS** en cada partición: el rol RO solo ve filas cuyo `client_types` se cruza con la variable de sesión
     `app.client_types`, que `PgChunkStore` fija con `set_config(..., true)` dentro de la transacción. Sin la
     variable, la base devuelve **0 filas** (falla cerrado);
   - filtro duro `client_types ∩ {client_type, "common"}` en el `WHERE`, más una verificación posterior a la
     recuperación (`violates_filter`) que emite `security_event`.

   Si alguna vez aparece `security_event` o ventas devuelve contenido técnico, es un **bug grave**: avisá antes
   de tocar nada.
3. **La KB se gestiona desde el panel y su fuente de verdad es `kb_documents`; nunca el contenido decide quién lo ve.**
   - `audience` se elige al subir el archivo (o sale de la carpeta de primer nivel, en los que vinieron del repo) y
     **no se cambia después**: es la partición de aislamiento más fuerte.
   - `client_types` = lo que el equipo tilda en el panel (`/admin` → Archivos), guardado en `kb_documents` y copiado
     a cada fragmento. En los archivos del repo, la subcarpeta es solo la sugerencia inicial; nada lo pisa después.
   - El loader rechaza el documento si el frontmatter define `audience` o `client_type(s)` (también al editarlo en
     el panel).
   - **El bot nunca usa un borrador**: lo que la IA reorganiza queda en `draft_markdown` hasta que una persona lo
     revisa y lo publica. Solo se publica directo, sin revisión, lo que ya vino con buen formato.
   - Los documentos subidos desde el panel **solo existen en la base**, no en git: el volumen hay que respaldarlo
     (§10.4).
4. **Las URLs las pone el código.** El LLM nunca: `strip_urls` las quita.
   - En ventas, `finalize_sales` agrega el CTA con UTM y verifica con `assert` que sea la **única** URL.
   - Las respuestas fijas de `nodes/redirects.py` hacen lo mismo.
5. **Solo se responde con lo que dicen los fragmentos.** Nada de cifras, diluciones, tiempos ni códigos inventados.
   `check_grounding` lo controla en tres capas: citas válidas, regex de cifras y verificador LLM.
   **No relajar el verificador** para bajar latencia o rechazos.
6. **Seguridad primero y sin LLM.** `safety_gate` (keywords + regex de `config/safety.yaml`) corre antes que todo y
   responde con un texto fijo.
   - No agregar "cloro" a las keywords: "x4 tiene cloro?" tiene que responderse.
   - Los teléfonos de emergencia (107, 911, 0800-333-0160) son de Argentina y están pendientes de confirmar con
     el responsable de seguridad de la empresa.
7. **Ventas nunca da contenido técnico** (dosis, diluciones, procedimientos): `technical_question` en ventas va a
   una respuesta fija con CTA, sin pasar por el RAG.
8. **La charla siempre se limita al negocio** y vuelve a él. `converse` no responde temas ajenos.
9. **No inventar contenido de la KB.** Si falta información, se reporta: la cargan los operadores (ver §11).
10. **Secretos**: `GOOGLE_API_KEY` vive en `ECO-KB/.env` (fuera de git). Nunca imprimirla ni commitearla. Ojo:
    `docker compose config` la expone.
11. **Git**: el commit y el push los hace el usuario. No commitear salvo pedido explícito.

---

## 2. Ruteo de tareas: qué leer y qué tocar

| Si la tarea es… | Leé primero | Palanca habitual |
|---|---|---|
| Una respuesta del bot es mala (no responde, inventa, rechaza de más) | §7, `ECO-KB/AGENTE-DEBUG.md` | Config/KB → prompts → código (en ese orden) |
| El bot no entiende jerga, typos o abreviaturas | §6.1 | `domain.glossary` / `description` en `config/clients.yaml` |
| El bot ofrece ejemplos que después no sabe responder | §6.1 | `capabilities` (generales o por cliente) en `clients.yaml` |
| Cambiar tono, CTA o textos de fallback por cliente | §6.1 | `clients.<tipo>` en `clients.yaml` |
| Falso positivo o negativo de seguridad | §6.2 | `config/safety.yaml` + `tests/unit/test_safety_gate.py` |
| La derivación a humano se dispara de más o de menos | §5.3, §6.3 | `config/handoff.yaml` + `tests/unit/test_handoff_gate.py` |
| Agregar o cambiar documentos de la KB | §8.2 | **Panel → Archivos** (subir, revisar, publicar). El repo (`docs/estructurados` + ingesta) es legado |
| Un archivo subido quedó en error o con un borrador raro | §8.5 | Ver `error`/`review` del documento; `ingest/format_check.py`, prompts de `restructure.py` |
| Un cliente no ve un documento (o ve uno que no debería) | §8.2 | Visibilidad en el panel (`/admin` → Archivos) |
| Agregar un tipo de cliente | §8.4 | `ClientType` en `state.py` + `clients.yaml` + carpetas |
| Cambiar el panel o el chat (UI) | §5 | `chat-view-test/src/{admin,main}.js`, `*.css` |
| Un endpoint nuevo o un cambio de contrato | §4 | `api/main.py`, `api/admin.py`, `api/schemas.py` |
| Latencia | §3.3 | `LLM_GENERATOR_THINKING`; nunca relajar el verificador |
| Deploy o problemas en el servidor | §10 | `compose.yml` (raíz), `ECO-KB/.env` |
| Resetear datos | §10.4 | `TRUNCATE` (conserva la KB) o `down -v` (borra todo) |

---

## 3. Arquitectura del backend

### 3.1 Pipeline de un turno (grafo principal, `graph/builder.py`)

```
validate_session → safety_gate ─┬─ unsafe → safety_response                      [texto fijo, sin LLM; fallback_reason=safety]
                                └─ safe → handoff_gate ─┬─ pide persona → handoff_response [texto fijo, sin LLM; intent=handoff]
                                                        └─ no → classify_intent ─┬─ business_question ───────────→ router
                                                                                 ├─ technical_question ─ support → router
                                                                                 │                     └ sales   → sales_technical_response [fijo + CTA; fallback_reason=technical]
                                                                                 ├─ purchase → purchase_response [fijo + CTA; marca derivación "purchase"]
                                                                                 └─ greeting|smalltalk|capabilities|off_topic|unclear → converse
router ─┬─ is_registered → support_rag → finalize_support (fuentes o fallback)
        └─ else          → sales_rag   → finalize_sales   (sin URLs del LLM + CTA)

*_rag (subgrafo Self-RAG, uno por flujo, cada uno solo conoce SU store):
  rewrite_query → retrieve → grade_documents ─┬─ hay relevantes → generate → check_grounding → check_answer → finalize
                                              └─ ninguno: reintenta rewrite (hasta MAX_RET=2) → no_answer → finalize
  check_grounding / check_answer fallan → generate con feedback (hasta MAX_GEN=3) → no_answer
```

Todo nodo con LLM tiene un comportamiento de respaldo si el LLM falla:

| Nodo | Si el LLM falla… |
|---|---|
| `classify_intent` | asume `business_question` |
| `converse` | usa una respuesta enlatada |
| `rewrite_query` | busca con el mensaje original |
| `grade_documents` | marca todos como relevantes |
| `generate` | cuenta como intento fallido |
| `check_grounding` | trata la respuesta como no respaldada |
| `check_answer` | la acepta |

### 3.2 Nodos y su palanca

| Nodo | Archivo | Qué hace | Palanca |
|---|---|---|---|
| `validate_session` | `nodes/session.py` | Fija los campos protegidos, arma el filtro, resetea los campos del turno, emite `turn_start` | Código (no tocar a la ligera) |
| `safety_gate` / `safety_response` | `nodes/safety.py` | Keywords/regex → texto fijo | `config/safety.yaml` |
| `handoff_gate` / `handoff_response` | `nodes/handoff.py` | Pedido explícito de una persona → texto fijo | `config/handoff.yaml` |
| `classify_intent` | `nodes/intent.py` | LLM: 8 intenciones (`graph/schemas.py:Intent`) | `prompts.INTENT` |
| `converse` | `nodes/intent.py` | Charla en 2–3 frases, redirige al negocio, sin URLs ni fuentes | `prompts.CONVERSE`, `capabilities` |
| `purchase_response`, `sales_technical_response` | `nodes/redirects.py` | Respuestas fijas + CTA | `messages.*` en `clients.yaml` |
| `rewrite_query` | `nodes/retrieval_nodes.py` | Pregunta interpretada (`intent`) + 2–4 consultas, usando historial, glosario y **catálogo real** de títulos/secciones (caché 5 min) | `prompts.REWRITE`, glosario |
| `retrieve` | `nodes/retrieval_nodes.py` + `retrieval/store.py` | Embebe las consultas + el mensaje original; por consulta, vector (HNSW coseno, 20) + FTS español (20) → RRF → top 8; entre consultas, RRF → top 10. Fija `app.client_types` para la RLS | `TOP_K`, `CANDIDATE_K`, `FUSED_TOP`, visibilidad en el panel |
| `grade_documents` | `nodes/retrieval_nodes.py` | Descarta `vec_score < MIN_VECTOR_SCORE` (0.45); LLM en lote, tolerante ("ante la duda, true") | `prompts.GRADE`, `MIN_VECTOR_SCORE` |
| `generate` | `nodes/generation.py` | Responde solo con los fragmentos y devuelve `cited_chunk_ids` | `prompts.generate_system` (`_COMMON_RULES`, `_FLOW_RULES`) |
| `check_grounding` | `nodes/generation.py` | (1) citas ⊂ docs; (2) regex de cifras con unidad, ratios y códigos (normaliza unidades y decimales); (3) LLM con título y sección | `check_numeric_claims`, `prompts.GROUNDING` |
| `check_answer` | `nodes/generation.py` | ¿Contesta la pregunta **interpretada**? | `prompts.ANSWER_CHECK` |
| `no_answer` | `nodes/finalize.py` | `fallback_reason`: `no_documents` / `ungrounded` / `answer_mismatch` | — |
| `finalize_support` / `finalize_sales` | `nodes/finalize.py` | Fuentes (dedup por título y sección) o texto de fallback; en ventas, CTA con UTM | `clients.<tipo>.fallback`, `cta` |

### 3.3 Parámetros

Están en `settings.py`; se pisan por env con el nombre en mayúsculas.

| Grupo | Variables |
|---|---|
| Modelos | `LLM_GENERATOR_MODEL`, `LLM_GRADER_MODEL` (formato `proveedor:modelo` de `init_chat_model`), `temperature=0` |
| Razonamiento | `LLM_GENERATOR_THINKING=low`. Medido el 2026-10-05: con `medium`, cada generación tarda 4–11 s; `minimal` no es más rápido y genera más borradores rechazados. `LLM_GRADER_THINKING=""` usa el valor del modelo |
| Recuperación | `MIN_VECTOR_SCORE=0.45`, `TOP_K=8`, `CANDIDATE_K=20`, `MAX_RET=2`, `MAX_GEN=3`. Constantes: `FUSED_TOP=10`, `MAX_QUERIES=4`, `RRF_K=60` |
| Embeddings | `gemini-embedding-001`, 768 dims, **normalizados L2 a mano** (Google no normaliza por debajo de 3072), tareas `RETRIEVAL_DOCUMENT` / `RETRIEVAL_QUERY`, reintentos ante 429 |
| Rutas | `CLIENTS_PATH`, `SAFETY_PATH`, `HANDOFF_PATH` (`config/*.yaml`); `KB_DIR` (por defecto `kb`, el ejemplo viejo: **usar `docs/estructurados`**) |
| Panel | `ADMIN_TOKEN` (por defecto `admin`; vacío = panel desactivado) |
| Drive (opcional) | `DRIVE_ROOT_FOLDER_ID`, `GOOGLE_OAUTH_*_PATH`, `DRIVE_SYNC_INTERVAL_MINUTES` |

**Latencia típica**: de 10 a 25 s en preguntas de negocio, porque el verificador a veces rechaza el primer borrador
por una paráfrasis (`llm_ungrounded`) y el reintento lo corrige. Es aceptado: **no relajar el verificador**.

### 3.4 Estado del grafo (`graph/state.py:GraphState`)

| Grupo | Campos |
|---|---|
| Sesión | `session_id`, `is_registered`, `client_type`, `language`, `message`, `flow`, `retrieval_filter` |
| Turno (los resetea `validate_session`) | `safety_flag`, `handoff_requested`, `intent`, `query` (pregunta interpretada), `queries`, `docs`, `relevant_docs`, `attempts_ret`, `attempts_gen`, `answer`, `cited_chunk_ids`, `feedback`, `grounding_ok`, `answer_ok`, `fallback_reason`, `final_answer`, `sources`, `cta_url` |
| Acumulados | `messages` (historial, reducer `add_messages`) y `audit_log` (reducer idempotente por `id`) |

La memoria entre turnos la guarda `PostgresSaver` (checkpointer de LangGraph) por `thread_id = session_id`.

### 3.5 `audit_log`: la traza de cada turno

Es la herramienta principal para depurar. Eventos, en orden posible:

`turn_start(flow, client_type, filter)` · `safety_gate(flagged)` · `safety_response` · `handoff_gate(requested)` ·
`handoff_response` · `classify_intent(intent, reason)` · `converse(intent)` · `purchase_response(cta_url)` ·
`sales_technical_response(cta_url)` · `rewrite_query(intent, queries, retry)` · `retrieve(queries, chunk_ids)` ·
`grade_documents(candidates, relevant_ids)` · `generate(attempt, cited[, error])` ·
`check_grounding(ok, reason[, missing])` · `check_answer(ok, reason)` · `no_answer(reason)` ·
`finalize_support` · `finalize_sales(cta_url)` · `security_event(dropped)`.

`security_event` **no debería aparecer nunca**: es una incidencia de aislamiento.

`check_grounding.reason` puede valer `no_citations`, `unknown_citations`, `numeric_mismatch` (con `missing`),
`llm_ungrounded` o `grader_error`.

---

## 4. Contrato de la API (`ECO-KB/src/eco_kb/api/`)

### 4.1 `POST /chat`

El body **es** el payload de sesión (un mock de la futura sesión real; el front lo manda en cada turno):

```json
{"session_id": "uuid", "is_registered": true, "client_type": "hotel|bodega|restaurante|generic", "language": "es", "message": "..."}
```

Respuesta (`ChatResponse`):

| Campo | Contenido |
|---|---|
| `answer` | Texto. Vacío si atiende una persona |
| `flow` | `support` / `sales` |
| `intent` | `business_question`, `technical_question`, `purchase`, `greeting`, `smalltalk`, `capabilities`, `off_topic`, `unclear`, `safety`, `handoff`, `human_agent` |
| `sources` | `[{title, section}]` |
| `fallback_reason` | `null` (respondió), `no_documents`, `ungrounded`, `answer_mismatch`, `safety`, `off_topic`, `technical` |
| `cta_url` | URL del CTA (en ventas) |
| `audit_log` | Solo los eventos del turno actual |
| `handoff` | `{status: bot\|pending\|human, requested_now, reason: user_request\|purchase\|repeated_fallback, notice}` |
| `message_id` | Id del mensaje registrado |

Un `client_type` fuera de la lista devuelve 422. Si la conversación está en `human`, guarda el mensaje del cliente
**sin invocar el grafo** y devuelve `answer=""` con `intent="human_agent"`.

### 4.2 Otros endpoints

| Ruta | Auth | Uso |
|---|---|---|
| `GET /health` | — | `SELECT 1` en la base (healthcheck de Docker) |
| `GET /chat/{session_id}/updates?after=<id>` | El `session_id` (UUID) hace de credencial | Polling del chat: estado + mensajes `human`/`system` con id > `after` |
| `GET /admin/ping` | Bearer | Login del panel |
| `GET /admin/stats` | Bearer | Métricas |
| `GET /admin/conversations?status=&flow=&client_type=&q=&issues=&limit=&offset=` | Bearer | Lista (las `pending` primero) |
| `GET /admin/conversations/{id}` | Bearer | Conversación con todos sus mensajes |
| `POST /admin/conversations/{id}/status` `{status: human\|bot, author}` | Bearer | Tomar / devolver o descartar |
| `POST /admin/conversations/{id}/messages` `{content, author}` | Bearer | Responder como persona (toma la conversación si hace falta) |
| `PUT /admin/messages/{id}/review` `{rating: good\|bad\|null, note, author}` | Bearer | Calificar una respuesta del bot |
| `GET /admin/documents` | Bearer | `{client_types: [{id, label}], documents: [...]}`. Los tipos salen de `clients.yaml` (+ `common` = Todos) |
| `PUT /admin/documents/visibility` `{audience, source_id, client_types, author}` | Bearer | Cambiar qué tipos consultan un archivo. 422 si un tipo no existe; `common` se normaliza a `["common"]`; `[]` = sin habilitar. Vacía la caché del catálogo. El `source_id` va en el body (o en la query) porque tiene barras |
| `POST /admin/documents/upload` `{audience, filename, content_base64, client_types?, source_id?, author}` | Bearer | 202 + documento en `processing`; se procesa en segundo plano (`BackgroundTasks`). Con `source_id` reemplaza ese documento; con el mismo nombre de archivo, también. 422: formato, tamaño, base64 o tipo inválido |
| `GET /admin/documents/detail?audience=&source_id=` | Bearer | Documento + `markdown` publicado, `draft_markdown` y `review` |
| `GET /admin/documents/original?audience=&source_id=` | Bearer | Descarga el archivo subido (404 si vino del repo) |
| `PUT /admin/documents/draft` `{audience, source_id, markdown, author}` | Bearer | Guarda una edición como borrador y devuelve el chequeo de formato. 422 si no se podría cargar (p. ej. frontmatter con `client_types`) |
| `POST /admin/documents/publish` `{audience, source_id, author}` | Bearer | Publica el borrador: trocea, embebe solo lo nuevo y vacía la caché del catálogo |
| `POST /admin/documents/discard-draft` | Bearer | Descarta el borrador (si nunca se publicó, borra el documento) |
| `POST /admin/documents/delete` | Bearer | Borra el documento y sus fragmentos |

Bearer = `Authorization: Bearer <ADMIN_TOKEN>`, comparado con `secrets.compare_digest`. Sin token configurado
→ 503; token incorrecto → 401. FastAPI expone `/docs` en la API (`:8000`, solo localhost en el servidor).

---

## 5. Panel de administración y derivación a humano

### 5.1 Registro de conversaciones (`src/eco_kb/conversations.py`)

- Cada turno de `/chat` se copia a `chat_conversations` + `chat_messages` (esquema en §8.1). Es una copia legible
  para el panel; la memoria del bot sigue siendo el checkpointer.
- Usa el pool **admin**: los roles de solo lectura del RAG no ven estas tablas.
- La API crea las tablas al arrancar (`CREATE TABLE IF NOT EXISTS`). No hay migraciones.
- Todas las llamadas al registro pasan por `_safe()`: si fallan, se loguean y **el chat sigue funcionando**.
- Si el grafo lanza una excepción, se registra un mensaje `system` de error y se relanza (HTTP 500).

### 5.2 Panel (`chat-view-test/admin.html` + `src/admin.{js,css}`)

- Login con contraseña y nombre. El token se guarda en `sessionStorage`, el nombre en `localStorage`.
- Métricas, lista con filtros y detalle con la **traza legible del pipeline** (función `trace()` en `admin.js`,
  que mapea los eventos del `audit_log`). Si agregás un evento nuevo al grafo, agregalo también a `trace()`.
- Revisión 👍/👎 con nota. "Copiar caso de eval" genera una línea para `evals/questions.yaml`.
- Enlaces directos con `#<session_id>`: escucha `hashchange`.
- **Vista Archivos** (`#archivos`, botón "Archivos" del header): **toda la base de conocimiento**.
  - **Subir** (arrastrar o elegir; PDF, DOCX o MD, hasta 15 MB): se elige soporte o ventas y, opcionalmente,
    los tipos de cliente. El archivo va en base64 (sin multipart) y se procesa en segundo plano. La vista consulta
    cada 3 s mientras haya algo "Procesando…" y avisa con un toast cuando termina.
  - **Estado por documento**: Procesando / Error (con el motivo) / Borrador para revisar (· N alertas) / Publicado
    / Publicada la versión anterior (hay un borrador nuevo).
  - **Acciones por fila**: casillas de visibilidad + "Guardar"; "Ver y editar" o "Revisar borrador"; "Reemplazar
    archivo" (conserva la visibilidad; si el nuevo necesita IA, la versión publicada sigue hasta que se publique el
    borrador); "Descargar original"; "Eliminar".
  - **Editor** (`<dialog>`, pantalla completa en el celular): la revisión automática (alertas, secciones que salen
    de imágenes, pendientes), el Markdown editable, la vista previa (títulos y tablas) y los botones "Guardar
    borrador", "Publicar" (pide confirmación si quedan alertas) y "Descartar borrador".
  - Búsqueda y filtro "¿Qué ve…? [tipo]". En esta vista se pausa el refresco de conversaciones. Los tipos vienen
    de la API, no están hardcodeados.

### 5.3 Derivación (último recurso: el bot tiene libertad para responder)

| Motivo | Detección | Respuesta al cliente |
|---|---|---|
| `user_request` | `handoff_gate`: keywords/regex de `handoff.yaml`, sin LLM, después de seguridad | `handoff.yaml:response` (sin CTA, también en ventas) |
| `purchase` | `classify_intent=purchase` → `purchase_response` pone `handoff_requested=true` | `clients.yaml:messages.purchase` + CTA |
| `repeated_fallback` | La API (`_handoff` en `api/main.py`) cuenta los fallbacks seguidos de `fallback_reasons` en `chat_messages`; dispara con `consecutive_fallbacks` (3) | La respuesta normal; la vista muestra `notice` aparte |

Estados de una conversación:

| Estado | Qué pasa |
|---|---|
| `bot` | Normal. |
| `pending` | Derivada: el bot **sigue respondiendo** y aparece primero en el panel. Solo se pasa a `pending` desde `bot` (no se repite). |
| `human` | Alguien la tomó en el panel: el bot no responde. |

De `human` se vuelve a `bot` con "Devolver al bot"; de `pending`, con "Descartar".

Al tomar o devolver una conversación se inserta un mensaje `system` visible para el cliente. Descartar una
derivación pendiente no se anuncia.

### 5.4 Actualización por polling (sin WebSockets ni SSE)

- **Chat** (`main.js`):
  - Desde el primer turno, consulta `/updates?after=<último id>` cada 8 s (`bot`), 5 s (`pending`) o 3 s
    (`human`).
  - Consulta **siempre**, aunque no haya derivación: el admin puede intervenir en cualquier conversación. Este bug
    ya se corrigió una vez (commit `019e02e`).
  - Con la pestaña oculta no consulta. Al volver a estar visible (`visibilitychange`), recibir el foco o recuperar
    la red (`online`), consulta en el momento: el celular congela los temporizadores.
  - Los ids son crecientes, así que no hay duplicados. Nunca hay dos consultas en paralelo.
- **Panel**: cada 5 s, con la pestaña visible. Solo redibuja si cambia la firma de los datos. No pisa una nota a
  medio escribir y conserva el borrador y el scroll.
- Si hiciera falta tiempo real: SSE alimentado por `LISTEN/NOTIFY` de Postgres, manteniendo `after=<id>`.

### 5.5 Limitaciones conocidas

- Los mensajes humano↔cliente no entran al historial del grafo: al devolver la conversación, el bot no los conoce.
- Si el cliente recarga el chat, empieza una sesión nueva (el `session_id` vive en memoria de la página).
- La contraseña es compartida y fácil a propósito; no hay usuarios.

---

## 6. Configuración (sin tocar código)

Toda la config se valida al arrancar (`config.py`, pydantic): **si falta algo, la API no arranca**.

### 6.1 `ECO-KB/config/clients.yaml`

- **`domain.description`**: contexto del negocio que reciben **todos** los nodos con LLM.
- **`domain.glossary`**: productos, equivalencias (inodoro = wc, paño = microfibra, X4 = desincrustante…),
  abreviaturas (q, pa, xq) y la regla de los dos equipos de ozono:
  - "Ozonify" a secas es el equipo del tipo de cliente: en bodega, el **Carro Ozonify Industrial**; en hotel o
    restaurante, **OZONIFY PRO**.
  - `converse` no recibe el glosario, para no ofrecer temas sin documentar.
- **`domain.capabilities.{support,sales}`**: qué dice el bot que sabe hacer. **Solo ejemplos que el bot realmente
  responde.** Se pisa por cliente con `clients.<tipo>.capabilities` (hoy lo hacen `bodega` y `hotel`).
- **`messages.sales_technical`** y **`messages.purchase`**: textos fijos. El código agrega el CTA después.
- **`clients.<tipo>`** (los 4 son obligatorios): `tone`, `cta.{text, base_url}`, `fallback.{support, sales}` y
  `capabilities` (opcional).
  - Las `base_url` hoy son placeholders: `https://ecoclean.example/...`.
  - El tono de bodega **no** fuerza vocabulario enológico: provocaba frases inventadas.

### 6.2 `ECO-KB/config/safety.yaml`

`keywords` (subcadenas, sin distinguir mayúsculas), `patterns` (regex) y `response`. Es conservador a propósito:
no relajar para mezclas, ingestión, ojos/piel ni ventilación. Cada cambio necesita un caso en
`test_safety_gate.py`.

### 6.3 `ECO-KB/config/handoff.yaml`

`enabled`, `keywords`, `patterns` (tres regex: hablar/contactar/derivar + persona; `pas[aá]s… con` para el voseo;
`me atiend…`), `response`, `consecutive_fallbacks`, `fallback_reasons` y `notice`.

`test_eval_questions_never_trigger_handoff` verifica que **ninguna pregunta de la batería** dispare la derivación.
Si cambiás patrones, corré `pytest`.

---

## 7. Depurar respuestas del bot (resumen; detalle en `ECO-KB/AGENTE-DEBUG.md`)

### 7.1 Método (obligatorio)

1. **Reproducir con el `audit_log`, al menos 3 veces.** Los LLM no son deterministas: un caso que falla 1 de 3
   veces es un problema real.
2. Ubicar **la etapa** que falla y cambiar **una sola palanca**, en este orden: config o contenido de la KB →
   prompts → código (último recurso).
3. **Agregar el caso a `evals/questions.yaml` antes de arreglarlo.**
4. Medir **antes y después** con `evals --runs 3` y `pytest`. Si algo empeora, revertir.
5. Entregar: causa raíz con evidencia (la traza), cambio aplicado, evals antes y después, riesgos y pendientes.
   Recordarle al usuario el deploy (§10.3).

### 7.2 Síntoma → causa → palanca

| Síntoma en la traza | Causa probable | Palanca |
|---|---|---|
| `no_documents` y un `rewrite_query` absurdo (p. ej. "x5" leído como el auto) | El reescritor no entiende el dominio | `domain.glossary` / `description` |
| `no_documents` con buenas consultas, y el chunk correcto no está en `retrieve.chunk_ids` | El contenido no existe o está enterrado | Documento (una sección por tema, títulos descriptivos) + volver a ingerir; o `MIN_VECTOR_SCORE` |
| El chunk está en `retrieve` pero no en `grade_documents.relevant_ids` | Evaluador estricto o `vec_score` bajo | `prompts.GRADE`, umbral |
| `ungrounded` / `numeric_mismatch` (ver `missing`) | Cifra inventada o mal normalizada | Comparar con el chunk; `_normalize` y `_UNIT_ALIASES` en `generation.py` |
| `ungrounded` / `llm_ungrounded` intermitente | Respuesta que mezcla fragmentos | Generador más conciso (prompt), **no** relajar el verificador |
| `answer_mismatch` con una respuesta correcta | Se juzgó contra el texto crudo | `prompts.ANSWER_CHECK` (pregunta interpretada + dominio) |
| `intent` equivocado | Clasificador | `prompts.INTENT` (desempate hacia `business_question`) |
| Ejemplos sugeridos que el bot no sabe responder | `capabilities` | `clients.yaml` |
| Bloqueado por seguridad sin motivo | Keyword demasiado amplia | `safety.yaml` + test |
| `no_documents` para un tipo de cliente y el documento existe (otro tipo sí lo ve) | El archivo no está habilitado para ese tipo | Panel → Archivos ("¿Qué ve…? [tipo]"); tildar y guardar |
| `retrieve` devuelve 0 fragmentos para todo | La consulta no fijó `app.client_types` (RLS falla cerrado) | `_set_rls` en `retrieval/store.py` |
| Cambié un documento y no se refleja | No se volvió a ingerir, o caché del catálogo (5 min por proceso) | Ingestar / reiniciar |
| Cambié código o config y no cambia nada | El contenedor corre la imagen vieja | `docker compose up -d --build` |

### 7.3 Reproducir en el proceso (sin HTTP, con el código del disco)

```python
# desde ECO-KB/:  PYTHONPATH=. python -m uv run python script.py   (no llamar al script como un módulo de la stdlib)
import uuid
from evals.run import build
g = build()   # usa la base local; para varios turnos: build_graph(services, checkpointer=MemorySaver()) + configurable.thread_id
out = g.invoke({"session_id": uuid.uuid4().hex, "is_registered": True, "client_type": "hotel",
                "language": "es", "message": "q paño uso pa inhodoro?"}, {"recursion_limit": 25})
for e in out["audit_log"]: print({k: v for k, v in e.items() if k != "id"})
```

### 7.4 Formato de `evals/questions.yaml`

Tiene tres secciones: `support`, `sales` y `chat` (estos casos corren en **ambos** flujos). Campos por caso:

| Campo | Significado |
|---|---|
| `q` | La pregunta |
| `client_type` | Por defecto `hotel` |
| `expect` | Subcadenas, alcanza con que una aparezca en alguna sección citada |
| `fallback: true` | Debe negarse (`maybe` = puede negarse o no) |
| `intent` | Clasificación esperada (string o lista) |
| `contains` / `absent` | Texto que debe aparecer o no en la respuesta |

---

## 8. Datos: base de datos y base de conocimiento

### 8.1 Esquema Postgres (base `ecokb`)

| Tabla | Quién la crea | Contenido |
|---|---|---|
| `kb_chunks` (padre) → `kb_chunks_support`, `kb_chunks_sales` | `sql/001_init.sql` (solo con el volumen vacío) | PK `(audience, chunk_id)`. Columnas: `source_id`, `title`, `section_path`, `content`, `client_types text[]`, `doc_type`, `product`, `language`, `status`, `content_hash`, `embedding vector(768)`, `tsv` (FTS español generada) y `updated_at`. Índices HNSW coseno, GIN(tsv), GIN(client_types) y `source_id` por partición |
| `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations` | `PostgresSaver.setup()` al arrancar la API | Memoria del bot por `thread_id` = `session_id` |
| `chat_conversations` | `ConversationStore.setup()` al arrancar | PK `session_id`; `is_registered`, `flow`, `client_type`, `language`, `status` (CHECK `bot`/`pending`/`human`), `handoff_reason`, `handoff_at`, `assignee`, `created_at`, `updated_at` |
| `chat_messages` | Ídem | `id bigserial`, `session_id` (FK, cascade), `role` (CHECK `user`/`bot`/`human`/`system`), `content`, `author`, `flow`, `intent`, `fallback_reason`, `sources jsonb`, `cta_url`, `latency_ms`, `audit_log jsonb`, `rating` (CHECK `good`/`bad`), `review_note`, `reviewed_by`, `created_at` |
| `kb_documents` | Migraciones v1 y v2 de `kb_documents.setup()` (al arrancar la API y en cada ingesta) | **Fuente de verdad de la KB.** PK `(audience, source_id)`; `title`, `product`, `client_types text[]` (`{}` = sin habilitar), `origin` (`files` = vino del repo/Drive, `panel`), `markdown` (versión publicada), `draft_markdown` (borrador sin publicar), `review jsonb` (revisión automática), `state` (`processing`/`ready`/`error`) + `error`, `original bytea` + `original_name` + `original_sha256`, `published_at`, `updated_by`, `updated_at`, `created_at`. Los roles RO **no** tienen acceso |
| `kb_schema_migrations` | Ídem | Versiones aplicadas (`version`, `applied_at`). La migración toma un advisory lock y es idempotente |
| `kb_sources` | `drive/sync.py` (solo si se usa Drive) | Estado de la sincronización con Drive |

Roles:

| Rol | Permisos |
|---|---|
| Admin (`eco` en el servidor, `ecokb` en local) | Dueño de la base: ingesta, checkpointer, conversaciones |
| `rag_support_ro` / `rag_sales_ro` | Solo `SELECT` sobre su partición. Contraseñas por defecto `support_ro_dev` / `sales_ro_dev` |

Los crean `sql/000_bootstrap.sql` (parametrizado con `admin_user`) y `sql/init-db.sh`, **solo la primera vez**
(volumen vacío).

**Migración v1** (`src/eco_kb/kb_documents.py`, `MIGRATIONS`): crea `kb_documents`, quita el CHECK
`cardinality(client_types) > 0` de `kb_chunks`, siembra el manifiesto con lo ya ingerido, recalcula `content_hash`
en SQL (`sha256` del contenido; **no re-embebe**) y activa la **RLS** (`ENABLE ROW LEVEL SECURITY` + política
`kb_client_types` `FOR SELECT TO rag_<aud>_ro`). El admin es dueño de las tablas y no está sujeto a RLS.
**Migración v2**: agrega las columnas de gestión desde el panel (`origin`, `markdown`, `draft_markdown`, `review`,
`state`, `error`, `original*`, `published_at`). Lo existente queda como `origin = 'files'`. Al arrancar, la API pasa
a `error` lo que haya quedado en `processing` por un reinicio (`recover_interrupted`).

`sql/001_init.sql` no cambió: la migración es la fuente de verdad para bases nuevas y existentes. Para agregar otra,
sumá una versión a `MIGRATIONS`; no edites una ya aplicada.

### 8.2 Base de conocimiento: estructura y visibilidad

**Se gestiona desde el panel** (`/admin` → Archivos; `DocumentStore` en `kb_documents.py`). Ciclo de un archivo subido:

1. `upload` guarda el original (`origin = 'panel'`, `state = 'processing'`). `source_id` =
   `<soporte|ventas>/panel/<nombre>.md`: subir otro archivo con el mismo nombre en la misma audiencia lo reemplaza.
2. `process` (en segundo plano) corre `prepare()` (§8.5):
   - si el archivo **ya tiene buen formato**, se guarda tal cual y se **publica directo**, sin IA;
   - si no, la IA arma un **borrador** (`draft_markdown` + `review`) y nadie lo ve hasta que se publica;
   - los errores (p. ej. hace falta IA y no hay `GOOGLE_API_KEY`) quedan en `state = 'error'` + `error`.
3. Revisión en el editor: `save_draft` guarda las ediciones como borrador, `publish` trocea y embebe solo lo nuevo
   (vía `ingest(..., origin="panel")`) y `discard_draft` descarta (si nunca se publicó, borra el documento).
4. `delete` borra el documento y sus fragmentos.

**Legado, el repo** (`ECO-KB/docs/estructurados/<soporte|ventas>/<carpeta>/*.md`, con alias en español: `soporte`→
`support`, `ventas`→`sales`, `comun`→`common`): la ingesta desde archivos sigue funcionando (`origin = 'files'`) y
guarda el Markdown en `kb_documents` para poder editarlo en el panel.
- Al editar, publicar o reemplazar desde el panel un documento que vino del repo, pasa a `origin = 'panel'`, y
  desde ahí **la ingesta desde archivos lo saltea** (stat `panel`) y nunca lo borra.
- Los PDF originales están en `ECO-KB/docs/`; `kb/` es un ejemplo viejo y no se usa.

**Visibilidad** (quién consulta cada archivo):

- La **audiencia** sale de la carpeta de primer nivel y es fija.
- Los **tipos de cliente** se eligen en el panel (`/admin` → Archivos) y viven en `kb_documents`. Se aplican al
  instante: el `PUT` actualiza el manifiesto y los fragmentos en una transacción, sin embeber, y vacía la caché del
  catálogo del reescritor.
- La subcarpeta solo es la **sugerencia inicial** de un archivo nuevo: un tipo (`hotel/`) → ese tipo; `comun/` →
  `common` (todos); cualquier otra (`equipos/`) → `[]`, **sin habilitar** (ningún cliente lo consulta).
- Un archivo vive **una sola vez**. Para compartirlo entre tipos se tilda en el panel; no se copia.

Documentos actuales (visibilidad de la base local al 2026-10-06; la vigente se ve en el panel):

| Ruta | Audiencia | Tipos |
|---|---|---|
| `soporte/comun/X4 Desincrustante Ecológico - Ficha técnica.md` | Soporte | Todos |
| `soporte/comun/X5 Potenciador Oxidativo Ecológico - Ficha técnica.md` | Soporte | Todos |
| `soporte/hotel/ECO360 - Manual operativo hotelería.md` | Soporte | Hotel |
| `soporte/hotel/OZONIFY PRO - Equipo de ozono.md` | Soporte | Hotel + **Restaurante (tildado en el panel)** |
| `soporte/bodega/CARRO OZONIFY INDUSTRIAL - Ficha técnica.md` | Soporte | Bodega |
| `ventas/comun/ECO360 para hoteles premium - Análisis ejecutivo.md` | Ventas | Todos (el generador lo presenta como beneficio general "documentado en hotelería") |

Por lo tanto, en soporte:

| Tipo | Puede consultar |
|---|---|
| hotel | Manual + OZONIFY PRO + X4/X5 |
| restaurante | OZONIFY PRO + X4/X5 |
| bodega | Carro Ozonify Industrial + X4/X5 |
| generic | Solo X4/X5 |

`LEEME - Pendientes y dudas.md` está en la raíz de `estructurados/` y la ingesta **lo ignora**.

### 8.3 Ingesta (`src/eco_kb/ingest/`)

- **Loader**: el frontmatter es opcional (`title`, `doc_type`, `product`, `language`); prohíbe `audience` y
  `client_type(s)`. Si un documento es inválido, lo rechaza entero y el resto sigue. Una subcarpeta desconocida
  **no** es un error: el archivo entra sin habilitar.
- **Chunker**: corta por `#` y `##`. Cada fragmento lleva el encabezado `Título > Sección`. Tamaño máximo unos
  3200 caracteres, con unos 400 de solapamiento.
- **Hash**: `content_hash = sha256(contenido)`, **solo del contenido** (que ya incluye `Título > Sección`). Mover,
  duplicar o cambiar la visibilidad de un archivo no obliga a re-embeber. Coincide con
  `encode(sha256(convert_to(content,'UTF8')),'hex')` en SQL (lo usa la migración).
- **Run** (`ingest()`), en orden:
  1. aplica la migración si falta (`kb_documents.setup`);
  2. upsert en `kb_documents`: inserta las fuentes nuevas con la sugerencia de la carpeta; de las existentes solo
     actualiza `title`/`product` y **nunca** pisa `client_types`; borra las que ya no existen;
  3. aplica a cada fragmento la visibilidad del manifiesto;
  4. compara con lo guardado: todo igual → `unchanged`; mismo hash con otros metadatos → `meta_only` (UPDATE sin
     embeber); hash nuevo → reutiliza un embedding guardado con ese hash en cualquier fila (`reused`) y solo si no
     existe llama al embedder (`embedded`, una vez por texto);
  5. borra los huérfanos (`deleted`).
- **Escribir para el RAG**: una sección `##` por tema, títulos descriptivos con el nombre del producto, y cifras
  y unidades siempre escritas igual (`20 ml/L`, `2 %`). Si se mueve un documento de carpeta cambia su
  `source_id`: los fragmentos viejos se borran y los nuevos reutilizan el embedding, pero el manifiesto lo trata
  como **archivo nuevo** (vuelve a la sugerencia de la carpeta: revisar el panel).
- **Drive (opcional)**: `python -m eco_kb.drive.sync --once|--loop`. Espera la estructura
  `<raíz>/<soporte|ventas>/<carpeta>/<archivo>`, convierte PDF/DOCX/Google Docs a Markdown y tiene un lock
  consultivo. El compose de la raíz **no** lo levanta (solo `ECO-KB/compose.yml --profile drive`). Si se usa,
  los cambios de carpeta también hay que hacerlos en Drive.

### 8.4 Agregar un tipo de cliente

1. Agregarlo al `Literal` `ClientType` en `graph/state.py`.
2. Agregar su bloque en `clients.yaml` (sin él, la API no arranca).
3. Tildar el tipo nuevo en el panel (Archivos) en los documentos que deba consultar. Opcional: carpetas
   `soporte/<tipo>/` y `ventas/<tipo>/` para que los archivos nuevos lo traigan como sugerencia.
4. Agregar su etiqueta en `LABELS` de `kb_documents.py` (si no, se muestra el id capitalizado) y actualizar los
   `<select>` y los mapas `CLIENTS` en `chat-view-test` (`index.html`, `admin.html`, `admin.js`). La vista
   Archivos toma los tipos de la API.
5. Agregar casos a las evals.

### 8.5 Preparar documentos: formato y reorganización con IA (`ingest/format_check.py` + `ingest/restructure.py`)

El núcleo es `prepare(src, audiencia, ai, ...)`: de los bytes de un archivo a Markdown listo para la KB, más una
revisión estructurada (`review = {used_ai, reason, alerts, blocks: [{title, items, alert}]}`). Lo usan **el panel**
(cada archivo subido, en `DocumentStore.process`) y el comando de consola de abajo, que es lo mismo pero sobre
archivos del repo. En el panel, lo que tiene buen formato se guarda **tal cual vino** y al borrador de la IA se le
sacan los datos de procedencia (`without_provenance`), porque el original y su hash viven en la base.

```bash
# desde ECO-KB/
python -m uv run python -m eco_kb.ingest.restructure <archivos o carpetas> --audiencia soporte|ventas
    [--carpeta borradores] [--destino docs/estructurados] [--solo-validar] [--forzar]
```

Por cada `.md`, `.pdf` o `.docx`, **gastando IA solo cuando hace falta**:

1. Si el destino ya existe y salió de esta misma fuente (`fuente_sha256` en el frontmatter): `SIN CAMBIOS`, no
   hace nada.
2. Si el destino existe y fue editado a mano (`borrador_sha256` no coincide), o no lo generó este comando:
   `PROTEGIDO`, no lo pisa (salvo `--forzar`).
3. `check_format` (sin IA): título, una sección `##` por tema en documentos de más de 1500 caracteres, secciones de
   hasta `CHUNK_CHARS` (3200), sin restos de extracción de PDF (una palabra por línea, `�`/`(cid:N)`) ni páginas
   sin texto. Si pasa: `TAL CUAL`, se copia sin cambios (solo se agrega la procedencia al frontmatter). Los
   títulos genéricos y las cifras escritas de dos formas son **avisos**, no obligan a usar IA.
4. Si no pasa: `CON IA`. Gemini (`LLM_RESTRUCTURE_MODEL`, por defecto el del generador) reorganiza el documento.
   El PDF va entero, así que también lee las páginas que son imágenes. Después:
   - otra llamada verifica el borrador contra el original;
   - sin IA, se comparan las cifras en los dos sentidos (`check_numeric_claims`): agregadas (solo en secciones que
     salen de texto) y perdidas.

   Escribe `<nombre>.md` y `<nombre>.revision.txt` con las alertas, las secciones que salen de imágenes y los
   pendientes. El `.txt` no se ingiere.

- El destino por defecto, `<destino>/<audiencia>/borradores/`, no es un tipo de cliente: al ingerir, el borrador
  entra **sin habilitar**. Se publica revisándolo contra el original y tildándolo en el panel (o moviéndolo a
  su carpeta final).
- La IA **solo reorganiza**: no agrega, no resume y no corrige datos. Es la regla 9 aplicada a la carga. Nunca
  publicar un borrador sin revisión humana.
- `--solo-validar` no escribe ni usa IA: sirve para chequear una carpeta. Hoy los 6 documentos de
  `docs/estructurados` pasan tal cual.
- Probado el 2026-10-06 con los PDF originales: X4 (todo imagen) salió completo, incluida la sección
  "Compatibilidad" que la transcripción manual había omitido. El Carro Ozonify salió con el panel y el diagrama, que
  eran imágenes. Son 2 llamadas por documento (unos 30 s).

---

## 9. Desarrollo local (Windows + Git Bash/PowerShell)

### 9.1 Puesta en marcha

```bash
cd ECO-KB && docker compose -f compose.local-db.yml up -d       # Postgres local: contenedor eco-kb-db-1, 127.0.0.1:5433, admin ecokb:ecokb
cd ECO-KB && python -m uv sync
cd ECO-KB && KB_DIR=docs/estructurados python -m uv run python -m eco_kb.ingest.run
```

En el Claude desktop app, `.claude/launch.json` tiene las configuraciones `api` (uvicorn en `127.0.0.1:8000`) y `web`
(Vite en `:5173`, `API_URL=http://127.0.0.1:8000`): usar `preview_start` con esos nombres. Vite reenvía `/api/*` a
la API, sin CORS. El panel queda en `http://localhost:5173/admin`.

### 9.2 Comandos (desde `ECO-KB/`)

| Para | Comando |
|---|---|
| Tests sin red (126) | `python -m uv run python -m pytest -q` |
| Integración | `python -m uv run python -m pytest -m integration` ⚠️ **`test_isolation_pg`, `test_drive_sync`, `test_kb_documents_pg` y `test_kb_panel_pg` hacen `TRUNCATE kb_chunks, kb_documents`** (también borran los documentos subidos desde el panel): después hay que volver a ingerir y **volver a elegir la visibilidad en el panel** (se pierde; p. ej. Restaurante en OZONIFY PRO). Alternativa sin cuota: `pg_dump -Fc` antes y `pg_restore --clean` después (los 2 errores de constraints heredadas son esperables). `test_conversations_pg` es seguro (solo borra sus sesiones `test-*`) |
| Evals | `python -m uv run python -m evals.run --flow support\|sales --runs 3 [--only "texto"] [--show]` (gasta cuota) |
| Preparar documentos | `python -m uv run python -m eco_kb.ingest.restructure <archivos> --audiencia soporte\|ventas [--solo-validar]` (IA solo para los que no tienen formato; ver §8.5) |
| Consultar la base local | `docker exec eco-kb-db-1 psql -U ecokb -d ecokb -c "select chunk_id, section_path from kb_chunks_support"` |

### 9.3 Trampas conocidas

- `uv run <exe>` falla en Windows ("uv trampoline failed"): usar siempre `python -m uv run python -m <módulo>`.
- En `ECO-KB/.env`, las URLs de la base usan `127.0.0.1`, **no** `localhost`: `localhost` resuelve a IPv6 y Docker
  Desktop cuelga la conexión (el pool da timeout a los 15 s).
- En Git Bash, `docker ... -v ./x:/app/x` necesita `MSYS_NO_PATHCONV=1`; si no, la ingesta carga 0 documentos.
- `curl` rompe el JSON con tildes o ñ: probar la API con Python (`urllib`).
- No nombrar scripts como módulos de la stdlib (`inspect.py` rompió los imports).
- En heredocs de bash, evitar comillas simples desbalanceadas dentro de código Python: para editar archivos, usar
  la herramienta de edición.
- Vite en desarrollo recarga **todas** las pestañas al editar un JS sin HMR boundary; el chat pierde la sesión.
  Es normal en desarrollo.
- Si los puertos 8000 y 5173 los ocupa otra sesión, levantar en otros puertos (`--port`) en lugar de matar
  procesos ajenos.
- El certificado HTTPS de `secondskin.com.ar` está vencido: el navegador integrado no lo abre.

---

## 10. Servidor y despliegue

### 10.1 Topología (`compose.yml` en la raíz, proyecto `eco-demo`)

| Servicio | Contenedor | Imagen / build | Puertos | Notas |
|---|---|---|---|---|
| `db` | `eco-db` | `pgvector/pgvector:pg18` | ninguno publicado | Volumen `eco_db_data`. Init con `ECO-KB/sql/init-db.sh` solo con el volumen vacío. Admin `eco` / `${ECOKB_ADMIN_PASSWORD:-eco}` |
| `api` | `eco-demo-api-1` | `./ECO-KB` | `127.0.0.1:${API_PORT:-8000}` | `env_file: ECO-KB/.env` (obligatorio: `GOOGLE_API_KEY`, modelos). Las `DATABASE_URL_*` las fija el compose y pisan las del `.env`. Healthcheck `/health` |
| `web` | `eco-demo-web-1` | `./chat-view-test` (`vite build` + `vite preview`) | `${WEB_PORT:-8088}:8080` | Reenvía `/api` → `http://api:8000`; `allowedHosts: true`; sirve `/` (chat) y `/admin` |

- En el servidor, el puerto 8080 del host está ocupado: por eso la vista va en el 8088.
- `demo-eco/.env` es opcional (puertos y contraseñas; ver `.env.example`).
- El `Postgres` compartido del servidor (`postgres-server`, red `servicios_internos`) **no tiene pgvector y no se
  usa**, por decisión explícita. Puede tener basura de un bootstrap fallido (base `ecokb`, roles `eco` y `rag_*`).
- **Legado, no usar para la demo**: `ECO-KB/compose.yml` (API sola contra un Postgres compartido),
  `ECO-KB/scripts/bootstrap-db.sh` y la sección "Despliegue en el servidor" de `ECO-KB/README.md`.

### 10.2 Imágenes

- **API**: `python:3.12-slim` + `uv sync --frozen`. Copia `src`, `config` y `evals`. **No copia `docs/`** (está en
  `.dockerignore`): para ingerir hay que montarla.
- **Web**: `node:22-alpine`. Copia `index.html`, `admin.html`, `vite.config.js` y `src/`. Si agregás una página
  HTML, sumala al `COPY` del Dockerfile y a `build.rollupOptions.input` en `vite.config.js`.

### 10.3 Actualizar el servidor

```bash
cd /proyectos/eco-soporte-dev && git pull && docker compose up -d --build
```

`--build` hace falta **siempre** que cambie código o configuración, porque se copian a la imagen.

Los documentos se gestionan desde el panel: no hace falta ingerir nada al desplegar. Solo si cambiaron archivos del
repo (legado) hay que ingerir:

```bash
docker compose run --rm -e KB_DIR=docs/estructurados -v ./ECO-KB/docs:/app/docs:ro api python -m eco_kb.ingest.run
```

La ingesta imprime `embedded` / `reused` / `meta_only` / `unchanged` / `deleted` por audiencia: `embedded` es lo
único que gasta cuota. Los archivos nuevos quedan con la visibilidad sugerida por su carpeta: revisarlos en el
panel (Archivos).

Logs: `docker compose logs -f api web db`. Base: `docker exec -it eco-db psql -U eco -d ecokb`.

### 10.4 Resetear

| Objetivo | Comando | Efecto |
|---|---|---|
| Limpiar conversaciones y conservar la KB (lo habitual) | `docker exec eco-db psql -U eco -d ecokb -c "TRUNCATE chat_messages, chat_conversations, checkpoints, checkpoint_blobs, checkpoint_writes;"` | Panel vacío y bot sin memoria. Instantáneo, sin cuota. **No tocar `checkpoint_migrations`** |
| **Respaldar la KB** (hacerlo antes de cualquier reset) | `docker exec eco-db pg_dump -U eco -d ecokb -Fc -f /tmp/ecokb.dump && docker cp eco-db:/tmp/ecokb.dump ./ecokb-$(date +%F).dump` | Los documentos subidos desde el panel **solo existen en la base**. Restaurar: `docker cp` + `pg_restore -U eco -d ecokb --clean --if-exists` (2 errores de constraints heredadas son esperables) |
| Empezar de cero | `docker compose down -v` → `up -d --build` → ingerir | Borra **todo**, incluida la KB: los documentos del repo se recuperan ingiriendo (gasta cuota); **los subidos desde el panel se pierden** salvo que haya un respaldo |
| Error `password authentication failed` (se cambiaron las `ECOKB_*_PASSWORD` después de crear el volumen) | Para la demo: empezar de cero | Las contraseñas solo se aplican al crear el volumen |

---

## 11. Estado actual y pendientes

- **Huecos de contenido** (no inventar; lo cargan los operadores; detalle en `LEEME`):
  - no hay material comercial propio de restaurante, bodega ni genérico;
  - no hay documentos de ventas por producto;
  - faltan la ficha técnica de OZONIFY PRO y la dosificación de X3;
  - no hay fichas de X3, SURFACE PROTECTANT, BIO SANITIZER ni Blue Test;
  - partes del manual de hotelería eran imágenes (instrucciones de aplicación, desinfección de manos);
  - bodega no tiene procedimientos propios (depósitos, barricas).
- **Sugerencias de la vista de chat** (`EXAMPLES` en `main.js`):
  - al tocarlas, cambian el tipo de cliente a Hotel (el único con material propio);
  - "¿Qué dosis de X5 recomiendan?" está en modo ventas a propósito, para mostrar que ventas no da datos
    técnicos, pero en la demo parece una falla. Está pendiente decidir si pasarla a modo cliente o cambiarle el
    texto.
- **CTA**: las `base_url` son placeholders (`ecoclean.example`).
- **Seguridad**: falta confirmar los números de emergencia con el responsable de seguridad.
- **Panel**: ver las limitaciones en §5.5.

---

## 12. Mapa de archivos

```
demo-eco/
├─ AGENTS.md                      ← este archivo
├─ README.md                      despliegue conjunto, panel, reset de datos
├─ CONTEXTO-PROYECTO.md           traspaso histórico (puede estar desactualizado: manda este archivo)
├─ compose.yml                    db + api + web (despliegue de la demo)
├─ .env.example                   puertos y contraseñas del compose (opcional)
├─ .claude/launch.json            configs api/web para la vista previa del desktop app
├─ ECO-KB/                        API
│  ├─ AGENTE-DEBUG.md             playbook de depuración de respuestas
│  ├─ README.md                   uso de la API, panel/derivación (la sección de despliegue es legado)
│  ├─ Dockerfile · compose.local-db.yml (Postgres local) · compose.yml (legado)
│  ├─ .env.example (local) · .env.server.example (servidor) · .env (NO está en git)
│  ├─ config/                     clients.yaml · safety.yaml · handoff.yaml
│  ├─ sql/                        000_bootstrap.sql · 001_init.sql · init-db.sh
│  ├─ src/eco_kb/
│  │  ├─ api/                     main.py (/chat, /health, /updates, _handoff) · admin.py · schemas.py
│  │  ├─ graph/                   builder.py · edges.py · prompts.py · schemas.py · state.py · services.py
│  │  │  └─ nodes/                session · safety · handoff · intent · redirects · retrieval_nodes · generation · finalize · common
│  │  ├─ retrieval/               store.py (híbrida + RRF + catálogo) · filters.py
│  │  ├─ ingest/                  loader.py · chunker.py · run.py · format_check.py · restructure.py (preparar documentos)
│  │  ├─ drive/                   sync con Google Drive (opcional)
│  │  ├─ conversations.py         registro de conversaciones + derivación
│  │  ├─ kb_documents.py          manifiesto de visibilidad + migración (RLS, hash) + DocumentStore del panel
│  │  ├─ config.py · settings.py · llm.py · db.py
│  ├─ docs/estructurados/         KB fuente que se ingesta (+ LEEME de pendientes)
│  ├─ evals/                      questions.yaml · run.py
│  └─ tests/                      unit/ · e2e/ (fakes, sin red) · integration/ (Postgres, ⚠️ TRUNCATE kb_chunks, kb_documents)
└─ chat-view-test/                vista (Vite)
   ├─ index.html · admin.html · vite.config.js (proxy /api, multipágina) · Dockerfile
   └─ src/  main.js (chat + polling) · admin.js (panel) · shared.js · style.css · admin.css · tokens.css (paleta)
```

**Identidad visual de la vista**: amarillo `#FFD70A`, aqua `#7EBEC5`, gris violáceo `#514F5B`, tipografía Poppins,
modo oscuro con `prefers-color-scheme`. Los tokens están en `tokens.css`, compartido por las dos páginas.

---

## 13. Checklist de entrega

- [ ] `python -m uv run python -m pytest -q` en verde (y el de integración que corresponda, sabiendo que vacía la KB).
- [ ] Si tocaste el comportamiento del bot: evals `--runs 3` antes y después, con el delta reportado.
- [ ] Si tocaste la UI: verificada en el navegador (chat y/o panel, móvil y oscuro si cambió el layout) y
      `npm --prefix chat-view-test run build` sin errores.
- [ ] Si agregaste un evento al `audit_log`: también está en `trace()` de `admin.js` y en §3.5.
- [ ] Si cambiaste el esquema de la KB: nueva versión en `MIGRATIONS` (`kb_documents.py`), idempotente y sin
      re-embeber; nunca editar una versión ya aplicada.
- [ ] Si tocaste la recuperación: la consulta sigue fijando `app.client_types` (si no, la RLS devuelve 0 filas).
- [ ] Documentación actualizada (este archivo y el README que corresponda).
- [ ] Le recordaste al usuario: `git pull` + `docker compose up -d --build` en el servidor (+ ingerir si
      cambiaron documentos, y revisar la visibilidad de los archivos nuevos en el panel). El commit lo hace el
      usuario.
