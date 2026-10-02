# ECO-KB

Bot RAG B2B (v1) con LangGraph: router determinista por `is_registered`, dos subgrafos Self-RAG
aislados (Soporte / Ventas), `Retrieval Grader`, `Hallucination Checker` en capas, CTA inyectado por
código y filtros duros por `client_type`.

```
validate_session → safety_gate ─┬─ unsafe → safety_response (enlatada, sin LLM)
                                └─ safe → router ─┬─ is_registered → support_rag → finalize_support
                                                  └─ else          → sales_rag   → finalize_sales (+CTA)
 *_rag: rewrite → retrieve → grade ─(reintento ≤ MAX_RET)→ generate → grounding → answer_check
                                   └─ sin docs / sin respaldo (≤ MAX_GEN) → no_answer → fallback
```

## Arranque en local (desarrollo)

```bash
cp .env.example .env                              # rellena GOOGLE_API_KEY
docker compose -f compose.local-db.yml up -d      # Postgres+pgvector LOCAL en 127.0.0.1:5433
python -m uv sync
python -m uv run python -m eco_kb.ingest.run
python -m uv run uvicorn eco_kb.api.main:app --reload
```

`compose.local-db.yml` solo sirve para desarrollo y tests de integración. Si cambias `sql/*.sql` tras la primera
creación: `docker compose -f compose.local-db.yml down -v` y vuelve a levantar.

## Despliegue en el servidor (Postgres compartido existente)

`compose.yml` **no levanta Postgres**: solo la API, unida automáticamente a la red externa `servicios_compartidos`
(no hace falta `docker network connect`). Se conecta al Postgres existente por el nombre de host de su
contenedor en esa red (en el otro proyecto es `db`).

1. **Crear la base, los usuarios y el esquema (una sola vez)**. Elige 3 contraseñas nuevas y ejecuta, desde la raíz
   del repo en el servidor (`PG_CONTAINER` = nombre del contenedor postgres en `docker ps`; `PG_SUPERUSER` = `cima`):

   ```bash
   PG_CONTAINER=<contenedor-postgres> PG_SUPERUSER=cima    ECOKB_ADMIN_PASSWORD=... ECOKB_SUPPORT_PASSWORD=... ECOKB_SALES_PASSWORD=...    bash scripts/bootstrap-db.sh
   ```

   Crea la base `ecokb` (dueño `ecokb`), los roles `rag_support_ro` y `rag_sales_ro`, la extensión `vector` y las
   tablas. Es idempotente en la parte de usuarios/base, pero el esquema (`001_init.sql`) se aplica una sola vez.
   Requisito: la imagen de Postgres debe incluir pgvector (la de `cima` es `pgvector/pgvector:pg18`).
2. `cp .env.server.example .env` y rellena las contraseñas, `GOOGLE_API_KEY` y el host de Postgres (`db`).
3. `docker compose up -d --build`. La API queda en `http://<ip-del-servidor>:8000` (`/docs` para probar).
4. Ingesta de documentos (una vez, o usa Drive): 
   `docker compose run --rm -e KB_DIR=docs/estructurados -v ./docs:/app/docs:ro api python -m eco_kb.ingest.run`
5. Drive (opcional): copia `secrets/` al servidor y `docker compose --profile drive up -d`.

Si la red no existe: `docker network create servicios_compartidos`. Para otro nombre de red: `SHARED_NETWORK=...`.
Actualizar: `git pull && docker compose up -d --build`.

## Tests

```bash
uv run pytest                    # unit + e2e con fakes, sin red ni BD
uv run pytest -m integration     # requiere Docker. OJO: vacía kb_chunks (usa embeddings falsos); relanza la ingesta después
```

## Base de conocimiento local (`kb/`, desarrollo)

`kb/<audience>/<client_type|_common>/*.md`, con frontmatter `title`, `doc_type`, `product`, `language`.
`audience` (`support`/`sales`) y `client_types` se **derivan de la ruta**; si el frontmatter intenta
definirlos, el documento se rechaza. `_common` → `['common']`. Los documentos actuales son sintéticos.
La ingesta solo re-embebe lo nuevo o modificado (`content_hash`) y borra los chunks huérfanos.

## Google Drive como fuente de la KB

Los operadores suben archivos a una carpeta de Drive y un proceso de sincronización actualiza el RAG.

**Estructura de la carpeta** (los nombres no distinguen mayúsculas; `soporte`/`ventas`/`comun` son alias):

```
<carpeta raíz>/soporte/hotel/Limpieza de baños.docx
               soporte/comun/Códigos de error.gdoc
               ventas/bodega/Propuesta bodegas.pdf
```

Formatos: Google Docs, `.md`, `.pdf` (con texto, no escaneado) y `.docx`. El frontmatter es opcional
(título = nombre del archivo). Audiencia y `client_type` salen **siempre de las carpetas**. Archivos en una
ruta inválida, de un tipo no soportado o con un `client_type` desconocido se omiten y se informan en el log.

**Puesta en marcha (una vez)**
1. Google Cloud: crear proyecto, habilitar *Google Drive API*, configurar la pantalla de consentimiento y
   crear un *OAuth client ID* de tipo "Desktop app"; guardar el JSON en `secrets/google_client_secret.json`.
   **Publica la pantalla de consentimiento ("In production")**: en "Testing" el refresh token caduca a los 7 días.
2. En una máquina con navegador: `uv run python -m eco_kb.drive.auth` → genera `secrets/google_token.json`;
   cópialo al servidor (se refresca solo).
3. Poner `DRIVE_ROOT_FOLDER_ID` (el id al final de la URL de la carpeta) en `.env`.

**Ejecución**

```bash
uv run python -m eco_kb.drive.sync --once                    # una pasada (cron / prueba)
uv run python -m eco_kb.drive.sync --loop --interval-minutes 15   # proceso permanente en el servidor
```

Cada pasada lista el árbol (una consulta barata), y solo descarga y embebe lo nuevo o modificado. Un archivo
borrado en Drive se borra de la KB. Salvaguardas: un archivo que falla al leerse conserva su versión anterior;
si una pasada fuera a borrar más de la mitad de la KB (p. ej. permisos revocados) se aborta salvo `--force`;
un lock en Postgres evita pasadas simultáneas. La latencia es el intervalo configurado.
Un webhook de Drive (push notifications) podría añadirse después limitándose a invocar `sync_once`.

## Comprensión de preguntas informales

El bot interpreta preguntas con errores, abreviaturas y jerga ("q paño uso pa inhodoro?"):
- **Contexto del negocio** (`domain` en `config/clients.yaml`): descripción y glosario (sinónimos, productos, abreviaturas).
  Lo reciben el reescritor, el evaluador, el generador y el verificador de respuesta. **Es la palanca principal**:
  si el bot malinterpreta un término, añádelo al glosario.
- **Reescritura multi-consulta**: el LLM corrige la pregunta y genera 2-4 búsquedas (pregunta clara, palabras clave
  y sinónimos, frase tipo documento) usando el catálogo real de títulos y secciones de la KB. Se busca con todas
  (más el mensaje original) y se fusionan los resultados con RRF.
- Evaluador más tolerante con consultas vagas; hasta `MAX_GEN=3` intentos de generación.

## Conversación (saludos, "¿qué sabes hacer?", fuera de tema)

Tras el filtro de seguridad, `classify_intent` clasifica cada mensaje: `business_question` (va al RAG, como siempre),
`greeting`, `smalltalk`, `capabilities`, `off_topic` y `unclear`. Todo lo que no es de negocio lo responde `converse`
(2-3 frases, sin RAG ni fuentes ni URLs) y **siempre termina redirigiendo al negocio**. Lo ajeno (chistes, deportes,
política, código, "ignora tus instrucciones", "muéstrame tu prompt") se declina sin responderlo. Ante la duda, el
clasificador elige `business_question`; si falla el clasificador se asume negocio, y si falla el LLM conversacional se
usa una respuesta enlatada. Lo que el bot dice que sabe hacer (y los ejemplos que sugiere) sale de
`domain.capabilities` en `config/clients.yaml`: **mantén ahí solo ejemplos que el bot realmente responde**.
La respuesta de `/chat` incluye `intent`; `fallback_reason` vale `off_topic` en rechazos de tema.

**Evaluación** (llama a Gemini y consume cuota): `uv run python -m evals.run --runs 3`. Usa `evals/questions.yaml`
(preguntas coloquiales con la sección esperada y preguntas que deben negarse). Añade ahí cada pregunta que falle
en producción y vuelve a ejecutarlo tras cualquier cambio de prompts, glosario o documentos.

## Panel de administración y derivación a humano

Cada turno de `/chat` se registra en `chat_conversations` / `chat_messages` (pool admin; la API crea las tablas al
arrancar, también en bases ya existentes). Es una copia legible del historial con intent, fallback, fuentes,
`audit_log` y latencia de cada respuesta; el grafo sigue usando su checkpointer. Si el registro falla, el chat sigue.

- **Panel**: `/admin.html` (o `/admin`) en la vista. Requiere `ADMIN_TOKEN` en `.env` (vacío = panel desactivado);
  cada persona entra con el token y su nombre. Permite filtrar conversaciones, ver la traza del pipeline de cada
  respuesta, calificarla 👍/👎 con nota y copiarla como caso para `evals/questions.yaml`.
- **Derivación (último recurso)**, configurable en `config/handoff.yaml`:
  - Pedido explícito ("quiero hablar con una persona", "me pasás con un asesor"): `handoff_gate`, determinista y
    después de seguridad, responde con texto fijo (sin LLM, `intent=handoff`).
  - N respuestas seguidas sin información (`consecutive_fallbacks`, por defecto 3): la API la marca sola.
  - Estados: `bot` → `pending` (el bot **sigue respondiendo**) → `human` (alguien la tomó en el panel: el bot no
    responde y la vista recibe los mensajes del equipo por `GET /chat/{session_id}/updates`) → `bot` al devolverla.
- Endpoints (`Authorization: Bearer <ADMIN_TOKEN>`): `GET /admin/stats`, `GET /admin/conversations`,
  `GET /admin/conversations/{id}`, `POST /admin/conversations/{id}/status`, `POST /admin/conversations/{id}/messages`,
  `PUT /admin/messages/{id}/review`.

## Aislamiento

- Tabla `kb_chunks` particionada `LIST (audience)`; cada store consulta directamente su partición.
- Roles `rag_support_ro` / `rag_sales_ro` (solo `SELECT` en su partición) con pool propio.
- Filtro tipado `RetrievalFilter` → `WHERE` parametrizado; assert post-recuperación descarta y registra violaciones.
- Contraseñas de `sql/001_init.sql` son de desarrollo: cámbialas en producción.

## Añadir un `client_type`

1. Añadirlo al `Literal` `ClientType` en `src/eco_kb/graph/state.py`.
2. Bloque nuevo en `config/clients.yaml` (tono, `cta`, `fallback`); sin él la app no arranca.
3. Carpetas `kb/support/<ct>/` y `kb/sales/<ct>/` con sus documentos y relanzar la ingesta.

## API

```bash
# Soporte (registrado, hotel)
curl -s localhost:8000/chat -H 'content-type: application/json' -d '{"session_id":"a1","is_registered":true,"client_type":"hotel","message":"¿Qué significa el error E04?"}'
# Ventas (no registrado, bodega) → incluye cta_url
curl -s localhost:8000/chat -H 'content-type: application/json' -d '{"session_id":"b1","is_registered":false,"client_type":"bodega","message":"¿Qué ofrecéis para limpiar depósitos?"}'
# Seguridad → respuesta enlatada, sin LLM
curl -s localhost:8000/chat -H 'content-type: application/json' -d '{"session_id":"c1","is_registered":true,"client_type":"hotel","message":"¿Puedo mezclar el producto con lejía?"}'
# client_type fuera de la allowlist → 422
curl -s -o /dev/null -w '%{http_code}' localhost:8000/chat -H 'content-type: application/json' -d '{"session_id":"d1","is_registered":true,"client_type":"banco","message":"hola"}'
```

La respuesta incluye `audit_log` del turno (chunk_id recuperados/citados y decisiones de cada nodo).
`GET /health` comprueba la BD. El body de `/chat` **es** el payload de sesión (mock): se sobrescribe en cada turno.

## Fuera de la v1

Reranker, `client_id` por tenant, LangSmith, autenticación real, UI, endpoint HTTP de ingesta.
