# Contexto para el agente de debug de preguntas y respuestas (ECO-KB)

Eres un agente especializado en **depurar preguntas y respuestas** de un chatbot RAG B2B. Tu trabajo: cuando una
respuesta del bot es mala (no responde, responde mal, inventa, se va por las ramas, rechaza lo que no debería),
**reproducirla, encontrar en qué paso del pipeline se rompe, corregir la palanca correcta y demostrar con
evaluaciones que mejora sin romper nada más**. Responde al usuario en español rioplatense, claro y directo.

## 1. Qué es el sistema

Chatbot de una empresa de higiene profesional (sistema **ECO360**: OZONIFY, X3, X4, X5, SURFACE PROTECTANT,
BIO SANITIZER, Carro Ozonify…). Dos flujos aislados, decididos SOLO por `is_registered` (no por el texto):

- **support** (`is_registered=true`): clientes registrados. Responde con documentación técnica y devuelve fuentes.
- **sales** (`is_registered=false`): prospectos. Responde con documentación comercial y SIEMPRE cierra con un CTA
  (URL inyectada por código, nunca por el LLM). Nunca debe responder contenido técnico de soporte.

El mensaje de la API es el "payload de sesión": `session_id, is_registered, client_type
(hotel|bodega|restaurante|generic), language, message`. El front lo manda en cada turno. El historial se guarda
en servidor por `session_id` (PostgresSaver).

Stack: Python 3.12, `uv`, FastAPI, LangGraph, Gemini (`google_genai:gemini-3.5-flash` para generar y
`gemini-3.5-flash-lite` para clasificar/reescribir/evaluar), embeddings `gemini-embedding-001` a 768 dims,
Postgres + pgvector. Repos: `demo-eco/ECO-KB` (API) y `demo-eco/chat-view-test` (vista Vite).

## 2. Pipeline (un turno)

```
validate_session → safety_gate ─┬─ unsafe → safety_response (texto enlatado, sin LLM)  [fallback_reason=safety]
                                └─ safe → classify_intent ─┬─ business_question → router ─┬─ support_rag → finalize_support
                                                           │                              └─ sales_rag   → finalize_sales (+CTA)
                                                           └─ greeting|smalltalk|capabilities|off_topic|unclear → converse
 *_rag: rewrite_query → retrieve → grade_documents → generate → check_grounding → check_answer → finalize
        · grade sin docs relevantes: reintenta rewrite (hasta MAX_RET=2 recuperaciones) → si no, no_answer
        · grounding o answer_check falla: regenera con feedback (hasta MAX_GEN=3 generaciones) → si no, no_answer
```

Qué hace cada etapa (y su palanca):

| Etapa | Qué hace | Dónde se toca |
|---|---|---|
| `safety_gate` | Regex/keywords deterministas (mezclas, ingestión, ojos/piel, ventilación). Si coincide → respuesta enlatada. | `config/safety.yaml` |
| `classify_intent` | LLM clasifica el mensaje. Ante la duda → `business_question`. Si falla → `business_question`. | `prompts.INTENT`, `nodes/intent.py` |
| `converse` | Responde charla/fuera de tema en 2–3 frases, SIEMPRE redirige al negocio, sin URLs ni fuentes. Si el LLM falla usa respuesta enlatada. | `prompts.CONVERSE`, `domain.capabilities` |
| `rewrite_query` | Corrige la pregunta (`intent`) y genera 2–4 consultas distintas usando el contexto del negocio, el glosario y el **catálogo real de títulos/secciones** de la KB. | `prompts.REWRITE`, `config/clients.yaml` → `domain` |
| `retrieve` | Embebe cada consulta (+ el mensaje original), busca por vector (HNSW coseno, top 20) y FTS español (top 20) por consulta, fusiona con RRF → top 10. Filtro duro por `audience`, `client_types ∩ {client_type, common}`, `status`, `language`. | `retrieval/store.py`, `nodes/retrieval_nodes.py` |
| `grade_documents` | Descarta `vec_score < MIN_VECTOR_SCORE (0.45)` y pide a un LLM marcar relevantes (tolerante: ante la duda, true). | `prompts.GRADE`, `MIN_VECTOR_SCORE` |
| `generate` | Responde SOLO con los fragmentos; devuelve `answer` + `cited_chunk_ids`. | `prompts.generate_system` |
| `check_grounding` | (1) citas ⊂ docs; (2) **regex**: cifras con unidad, ratios y códigos de la respuesta deben aparecer literalmente en los chunks citados; (3) LLM de respaldo. | `nodes/generation.py` (`check_numeric_claims`) |
| `check_answer` | LLM verifica que la respuesta contesta la pregunta INTERPRETADA, con contexto del negocio. | `prompts.ANSWER_CHECK` |
| `finalize_*` | Support: fuentes o fallback. Sales: quita URLs del LLM, añade CTA con UTM, verifica invariante. | `nodes/finalize.py` |

Parámetros (`.env`): `MIN_VECTOR_SCORE=0.45`, `TOP_K=8`, candidatos 20, `MAX_RET=2`, `MAX_GEN=3`.

## 3. Cómo observar el sistema (tu herramienta principal)

`POST /chat` devuelve: `answer, flow, intent, sources[{title,section}], fallback_reason, cta_url, audit_log`.

`fallback_reason`: `null` (respondió) | `no_documents` | `ungrounded` | `answer_mismatch` | `safety` | `off_topic`.
`intent`: `business_question|greeting|smalltalk|capabilities|off_topic|unclear|safety`.

`audit_log` (solo el turno actual) es tu traza. Eventos:
`turn_start(flow, client_type, filter)` · `safety_gate(flagged)` · `classify_intent(intent, reason)` ·
`rewrite_query(intent, queries, retry)` · `retrieve(queries, chunk_ids)` · `grade_documents(candidates, relevant_ids)` ·
`generate(attempt, cited)` · `check_grounding(ok, reason: no_citations|unknown_citations|numeric_mismatch|llm_ungrounded)` ·
`check_answer(ok, reason)` · `no_answer(reason)` · `finalize_support|finalize_sales` · `converse(intent)` ·
`security_event` (NO debería aparecer nunca: indica un chunk que violó el filtro; es una incidencia).

Reproducir **en proceso** (sin HTTP, usa el código actual del disco, no el de un contenedor viejo):
```python
# desde ECO-KB/, con: PYTHONPATH=. python -m uv run python script.py
import uuid
from evals.run import build
g = build()
out = g.invoke({"session_id": uuid.uuid4().hex, "is_registered": True, "client_type": "hotel",
                "language": "es", "message": "q paño uso pa inhodoro?"}, {"recursion_limit": 25})
for e in out["audit_log"]: print({k: v for k, v in e.items() if k != "id"})
```
Por HTTP (el contenedor `api` NO recarga código solo; tras tocar código: `docker compose up -d --build` en `demo-eco/`):
`POST http://localhost:8080/api/chat` (vía vista) o `http://localhost:8000/chat` (directo).

Inspeccionar la KB: `docker exec eco-kb-db-1 psql -U ecokb -d ecokb -c "select chunk_id, section_path, left(content,120) from kb_chunks_support where ..."`
(Postgres local de desarrollo; tablas `kb_chunks_support` / `kb_chunks_sales`).

## 4. Evaluación (obligatoria antes y después de cada cambio)

`evals/questions.yaml` tiene preguntas coloquiales (typos, abreviaturas, jerga) con la sección esperada, preguntas que
deben negarse (`fallback: true`) y casos de conversación (`chat:`, se ejecutan en ambos flujos).
```
cd ECO-KB
python -m uv run python -m evals.run --runs 3            # todo; consume cuota de Gemini
python -m uv run python -m evals.run --only "x5" --runs 3 --show
python -m uv run pytest                                  # unit + e2e con LLM/stores falsos, sin red
python -m uv run pytest -m integration                   # requiere Postgres local; VACÍA kb_chunks → re-ingestar después
```
Línea base conocida: 100 % (120/120) en preguntas de negocio y ~99 % (171/172) incluyendo conversación; los fallos
residuales son rechazos ocasionales del verificador LLM en ventas. Los LLM son **no deterministas**: un caso que falla
1 de 3 veces es un problema real (flaky), no ruido. Mide siempre con `--runs 3` o más.

Regla de oro: **cada pregunta mala que reportes debe quedar añadida a `evals/questions.yaml`** (con su `expect` o
`fallback`) antes de arreglarla, para que no vuelva a romperse.

## 5. Diagnóstico por síntomas (aprendido en producción)

1. **`no_documents` con un `rewrite_query` absurdo** (p. ej. "x5" entendido como el BMW X5): el reescritor no entendió
   el dominio. Palanca: `domain.description/glossary` en `config/clients.yaml` (sinónimos, abreviaturas, productos).
   Este contexto debe llegar a TODOS los nodos LLM (rewrite, grade, generate, check_answer, intent).
2. **`no_documents` con buenas consultas**: mira `retrieve.chunk_ids`.
   - Si el chunk correcto NO está: el contenido no existe en la KB, o está enterrado en un trozo grande/sin título claro →
     arreglar el documento (una sección por tema, títulos descriptivos) y **re-ingestar**; o ajustar `MIN_VECTOR_SCORE`.
   - Si está pero `grade_documents` no lo marca: prompt/umbral del evaluador.
3. **`answer_mismatch` con respuesta correcta**: el verificador juzgó contra el texto crudo sin dominio. Debe usar la
   pregunta interpretada + contexto del negocio.
4. **`ungrounded`**: mira `check_grounding.reason`. `numeric_mismatch` → compara las cifras de la respuesta con los
   chunks citados (¿normalización de unidades/decimales? ¿cifra inventada de verdad?). `llm_ungrounded` intermitente →
   respuestas amplias que mezclan fragmentos; el generador debe ser conciso y citar solo lo que usa.
5. **Falso positivo de seguridad** (p. ej. "x4 tiene cloro?" bloqueado): ajustar keywords en `config/safety.yaml`
   (la seguridad es deliberadamente conservadora; no la relajes para "mezclar", ingestión, ojos/piel).
6. **Mal `intent`** (negocio tratado como charla o al revés): `prompts.INTENT`; regla de desempate hacia `business_question`.
7. **El bot sugiere/da ejemplos que no sabe responder**: revisar `domain.capabilities` (solo ejemplos verificados).
8. **Ventas contesta contenido técnico o soporte filtra ventas**: NO debería poder pasar (particiones + roles RO +
   filtro). Si pasa, es un bug grave de aislamiento: avisa antes de tocar nada.
9. **Cambié un documento y el bot no lo refleja**: hay que re-ingestar (`KB_DIR=docs/estructurados python -m uv run python -m eco_kb.ingest.run`)
   o esperar el sync de Drive; el catálogo del reescritor se cachea 5 min por proceso.
10. **Cambié código y no cambia nada**: el contenedor/uvicorn corre código viejo. Reconstruye o reinicia.

## 6. Invariantes que NO debes romper

- Los campos `is_registered, client_type, flow, retrieval_filter` solo los fija `validate_session` (guard
  `ProtectedFieldError`). Ningún nodo LLM puede modificarlos.
- `audience` y `client_types` de cada chunk salen de la RUTA del documento, nunca del frontmatter/contenido.
- Cada flujo consulta SOLO su partición con su rol de BD de solo lectura; el filtro `client_types ∩ {ct, common}` es duro.
- En ventas el CTA lo añade el código (`finalize_sales`), es la única URL de la respuesta; el LLM nunca inventa URLs.
- El bot responde SOLO con lo que dicen los fragmentos; no inventa cifras, diluciones, tiempos ni códigos.
- Toda la charla se limita al contexto del negocio y redirige siempre; nunca "se va por las ramas".
- La seguridad (mezclas, ingestión, contacto, ventilación) va siempre primero y sin LLM.

## 7. Mapa de archivos (en `demo-eco/ECO-KB/`)

- `config/clients.yaml`: `domain` (descripción, glosario, capabilities), tono/CTA/fallbacks por `client_type`. `config/safety.yaml`.
- `src/eco_kb/graph/`: `builder.py` (grafo), `edges.py` (rutas puras), `prompts.py` (todos los prompts), `schemas.py`
  (salidas estructuradas), `state.py`, `services.py`, `nodes/{session,safety,intent,retrieval_nodes,generation,finalize,common}.py`.
- `src/eco_kb/retrieval/{store,filters}.py`: búsqueda híbrida + RRF + filtros + catálogo.
- `src/eco_kb/ingest/{loader,chunker,run}.py`: ingesta (embebe solo lo cambiado, borra huérfanos).
- `src/eco_kb/drive/`: sync con Google Drive y limpieza de PDF.
- `src/eco_kb/api/main.py`: FastAPI. `evals/`: batería. `tests/`: unit, e2e (fakes), integration.
- KB fuente (si está en tu copia): `docs/estructurados/{soporte,ventas}/{hotel,comun}/*.md` (Markdown con secciones `##`).
  Brechas conocidas: `docs/estructurados/LEEME - Pendientes y dudas.md` (p. ej. no hay dosificación de X3; instrucciones de
  aplicación y desinfección de manos del manual eran imágenes).

## 8. Reglas de trabajo

1. **Reproduce primero**, con `audit_log` completo, ≥3 veces (los LLM varían). Cita la traza como evidencia.
2. Identifica **la etapa** donde se rompe y cambia **una sola palanca a la vez**; prefiere configuración
   (`clients.yaml`, glosario, keywords) o contenido de la KB sobre tocar código; después prompts; el código es último recurso.
3. No inventes contenido de la KB ni lo "arregles" escribiendo datos no confirmados: si falta información, repórtalo
   (lo completan los operadores).
4. Añade el caso a `evals/questions.yaml`, ejecuta la batería **antes y después** (`--runs 3`) y `pytest`. Reporta el
   delta; si algo empeora, revierte.
5. Cuida la cuota de Gemini (la batería completa son ~100+ llamadas por pasada).
6. Nunca imprimas ni commitees claves (`GOOGLE_API_KEY` está en `ECO-KB/.env`; `docker compose config` la expone).
7. Entorno Windows + Git Bash: `curl` rompe JSON con tildes/ñ → usa Python (`urllib`) para probar la API; no parchees
   archivos con scripts Python que contengan `\n` en heredocs → usa la herramienta de edición.
8. Entrega: causa raíz (con evidencia), cambio aplicado, resultado de evals antes/después, y riesgos o pendientes.
