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

`<kb>/<audience>/<carpeta>/*.md`, con frontmatter `title`, `doc_type`, `product`, `language` (la KB real está en
`docs/estructurados`; `kb/` tiene documentos sintéticos de ejemplo). `audience` (`support`/`sales`) se **deriva de
la ruta**. Los `client_types` (qué tipos de cliente consultan el archivo) se gestionan en el panel
(`/admin` → **Archivos**) y se guardan en la tabla `kb_documents`; la subcarpeta es solo la sugerencia inicial de
un archivo nuevo (`hotel/` → hotel, `_common`/`comun` → `['common']`, otra → sin habilitar). Si el frontmatter
intenta definir `audience` o `client_types`, el documento se rechaza.

La ingesta solo embebe contenido nuevo: el `content_hash` es solo del contenido, así que mover, duplicar o cambiar
la visibilidad de un archivo reutiliza el embedding guardado. Borra los chunks huérfanos. Imprime por audiencia
`embedded` (lo único que gasta cuota), `reused`, `meta_only`, `unchanged` y `deleted`.

Además del filtro del código, la base aplica **RLS** por tipo de cliente en cada partición: los roles de solo
lectura solo ven las filas de los tipos que fija `app.client_types` en la transacción (sin la variable, nada). La
crea la migración de `src/eco_kb/kb_documents.py`, que corre sola al arrancar la API y en cada ingesta.

## Preparar documentos nuevos (IA solo si hace falta)

Cuando llega un PDF o Word sin el formato que necesita el RAG:

```bash
python -m uv run python -m eco_kb.ingest.restructure "ruta/al/archivo.pdf" --audiencia soporte
```

- **Si el archivo ya tiene buen formato** (título, una sección `##` por tema, secciones de tamaño razonable, texto
  limpio), se usa **tal cual, sin IA**.
- **Si ya se procesó** y la fuente no cambió, no hace nada.
- **Si no tiene buen formato**, Gemini lo reorganiza (también lee las páginas que son imágenes) y otra llamada lo
  verifica contra el original. Deja `docs/estructurados/<audiencia>/borradores/<nombre>.md` y un
  `<nombre>.revision.txt` con lo que hay que revisar: cifras agregadas o perdidas, observaciones del verificador,
  secciones que salen de imágenes y pendientes.
- Los borradores entran a la KB **sin habilitar**. Se revisan contra el original y se publican tildándolos en el
  panel (Archivos). Un borrador editado a mano nunca se pisa (salvo con `--forzar`).
- `--solo-validar` revisa una carpeta sin escribir nada ni usar IA. El detalle está en `AGENTS.md` §8.5.

## Google Drive como fuente de la KB

Los operadores suben archivos a una carpeta de Drive y un proceso de sincronización actualiza el RAG.

**Estructura de la carpeta** (los nombres no distinguen mayúsculas; `soporte`/`ventas`/`comun` son alias):

```
<carpeta raíz>/soporte/hotel/Limpieza de baños.docx
               soporte/comun/Códigos de error.gdoc
               ventas/bodega/Propuesta bodegas.pdf
```

Formatos: Google Docs, `.md`, `.pdf` (con texto, no escaneado) y `.docx`. El frontmatter es opcional
(título = nombre del archivo). La audiencia sale **siempre de la carpeta** de primer nivel. La de segundo nivel
solo sugiere la visibilidad de un archivo nuevo (una carpeta que no es un tipo de cliente lo deja sin habilitar);
después se gestiona en el panel (`/admin` → Archivos). Archivos en una ruta inválida o de un formato no soportado
se omiten y se informan en el log.

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

Permite que la empresa revise cómo responde el bot y que una persona del equipo intervenga cuando hace falta.
La derivación es **último recurso**: el bot tiene libertad para responder y solo se calla cuando alguien toma
la conversación a mano.

### Uso

- Entrar a `http://<ip>:8088/admin` (en local, `http://localhost:5173/admin`) con la contraseña `admin` y tu
  nombre. El nombre firma las respuestas y las revisiones.
  - La contraseña se cambia con `ADMIN_TOKEN=...` en `ECO-KB/.env`. Si se deja vacía, el panel queda desactivado.
- Arriba están las métricas: conversaciones, derivaciones pendientes, % de respuestas sin información, 👍/👎 y
  latencia promedio.
- La lista se filtra por estado, flujo, tipo de cliente y texto, o "solo con problemas". Las derivadas aparecen
  primero.
- En el detalle de cada respuesta del bot se ven intención, fallback, latencia, cómo interpretó la pregunta,
  fuentes, CTA y una **traza legible del pipeline** (resumen del `audit_log`; el JSON completo está desplegable).
- **Revisar**: 👍/👎 con nota. **Copiar caso de eval** genera la línea para `evals/questions.yaml` (con las
  secciones citadas como `expect`, o `fallback: true` si el bot no respondió).
- **Intervenir**:
  - *Tomar conversación* hace que el bot deje de responder.
  - Responder desde la caja de abajo: si la conversación no estaba tomada, la toma sola.
  - *Devolver al bot* o *Descartar* (en una derivación pendiente) la devuelve al bot.
  - El cliente ve los mensajes del equipo en el chat sin recargar.
- **Archivos** (botón del header, o `/admin#archivos`): qué tipos de cliente consultan cada documento de la KB.
  - Lista agrupada en Soporte / Ventas, con título, ruta, producto y cantidad de fragmentos.
  - Casillas **Todos (común)**, Hotel, Bodega, Restaurante, Genérico y **Guardar** por fila. Sin ninguna tildada,
    el archivo queda **Sin habilitar** (el bot no lo consulta).
  - Los cambios aplican al instante, sin re-ingestar ni redeployar. Soporte y ventas son bases separadas: la
    audiencia la define la carpeta y no se cambia desde acá.
  - Filtros: búsqueda por título o ruta, y **¿Qué ve…? [tipo]** para revisar de un vistazo qué consulta, por
    ejemplo, un cliente de bodega.
  - Los archivos se siguen cargando por repo o Drive + ingesta; un archivo nuevo arranca con la visibilidad que
    sugiere su carpeta.

### Cómo está implementado

**Registro de conversaciones** ([`src/eco_kb/conversations.py`](src/eco_kb/conversations.py)). Cada turno de
`/chat` se guarda en dos tablas:

- `chat_conversations`: una fila por `session_id`, con flujo, tipo de cliente, estado de la derivación, motivo y
  quién la atiende.
- `chat_messages`: un mensaje por fila (`role`: `user | bot | human | system`). Las respuestas del bot guardan
  `intent`, `fallback_reason`, `sources`, `cta_url`, `latency_ms`, `audit_log` y la revisión del equipo
  (`rating`, `review_note`, `reviewed_by`).

Las tablas usan el pool **admin**: los roles de solo lectura del RAG no las ven, así que el aislamiento de la KB
no cambia. La API las crea al arrancar (`CREATE TABLE IF NOT EXISTS`), también en bases que ya existían, sin
migraciones manuales. Es una copia legible para el panel: el grafo sigue usando su checkpointer de LangGraph
como memoria. Si el registro falla, se loguea y el chat sigue respondiendo.

**Derivación**. Hay tres disparadores:

| Motivo | Cómo se detecta | Respuesta al cliente |
|---|---|---|
| `user_request` ("quiero hablar con una persona", "me pasás con un asesor") | `handoff_gate` en el grafo: determinista, sin LLM, después de `safety_gate`. Palabras y regex en [`config/handoff.yaml`](config/handoff.yaml) | Texto fijo (`response` de `handoff.yaml`), `intent=handoff` |
| `purchase` (quiere comprar, reponer o cotizar) | `classify_intent` devuelve `purchase` y `purchase_response` marca `handoff_requested` | Texto fijo (`messages.purchase` de `clients.yaml`) + CTA |
| `repeated_fallback` (N respuestas **seguidas** sin información) | En la API (`_handoff` en `api/main.py`), contando en `chat_messages` | La respuesta normal del bot; la vista muestra aparte el `notice` de `handoff.yaml` |

N es `consecutive_fallbacks` en `handoff.yaml` (3 por defecto; 0 = desactivado). Para que el patrón de pedido
explícito no robe preguntas reales, `tests/unit/test_handoff_gate.py` comprueba que ninguna pregunta de
`evals/questions.yaml` lo dispara. Si se agregan palabras o patrones, correr `pytest`.

**Estados**: `bot` → `pending` → `human` → `bot`.

| Estado | Qué pasa |
|---|---|
| `bot` | Normal. |
| `pending` | Derivada: aparece primero en el panel y **el bot sigue respondiendo**. Solo se pasa a `pending` desde `bot`, así que una derivación no se repite. |
| `human` | Alguien la tomó: `/chat` guarda el mensaje del cliente **sin invocar el grafo** y devuelve `answer=""`, `intent=human_agent`. |

Al tomarla o devolverla se inserta un mensaje `system` ("Ana se sumó a la conversación", "La conversación vuelve
al asistente virtual") que ven el cliente y el panel. Descartar una derivación pendiente no se anuncia al
cliente.

**Actualización (polling)**. No hay WebSockets ni SSE: cada lado pregunta cada pocos segundos. Así funciona sin
tocar el proxy de Vite ni el contenedor, y se recupera solo si la API se reinicia.

- **Chat** (`chat-view-test/src/main.js`):
  - Desde el primer mensaje del cliente consulta `GET /chat/{session_id}/updates?after=<último id>` cada 8 s
    (`bot`), 5 s (`pending`) o 3 s (`human`).
  - Consulta **siempre**, no solo con la conversación derivada, porque el equipo puede intervenir en cualquier
    conversación.
  - Con la pestaña oculta no consulta (batería). Al volver a estar visible, recuperar la red o recibir el foco,
    consulta en el momento: el celular congela los temporizadores con la pantalla apagada.
  - Los ids de `chat_messages` son crecientes, así que `after` evita duplicados. Una consulta no arranca si la
    anterior sigue en curso.
- **Panel** (`chat-view-test/src/admin.js`):
  - Cada 5 s (solo con la pestaña visible) refresca métricas, lista y la conversación abierta.
  - Solo redibuja si cambió algo (compara una firma de ids, estado y revisiones). No pisa una nota que se está
    escribiendo y conserva el borrador de respuesta y el scroll.
- Si hiciera falta tiempo real, el paso natural es SSE alimentado por `LISTEN/NOTIFY` de Postgres, sin cambiar
  el formato `after=<id>`.

**Seguridad**:

- Los endpoints `/admin/*` piden `Authorization: Bearer <ADMIN_TOKEN>` y comparan con `secrets.compare_digest`.
  Es una contraseña compartida para la demo, no hay usuarios.
- El panel la guarda en `sessionStorage` (se borra al cerrar la pestaña) y el nombre en `localStorage`.
- `GET /chat/{session_id}/updates` no pide contraseña: el `session_id` (UUID aleatorio) hace de credencial y
  solo devuelve mensajes `human` y `system`, nunca el `audit_log`.

**Archivos**:

| Archivo | Qué tiene |
|---|---|
| `src/eco_kb/conversations.py` | Esquema y `ConversationStore` |
| `src/eco_kb/api/main.py` | Registro en `/chat`, `_handoff` y `/chat/{id}/updates` |
| `src/eco_kb/api/admin.py` | Endpoints del panel |
| `src/eco_kb/kb_documents.py` | Manifiesto `kb_documents`, migración (RLS, hash) y `DocumentStore` (vista Archivos) |
| `src/eco_kb/graph/nodes/handoff.py` | `handoff_gate` y `handoff_response` |
| `src/eco_kb/graph/nodes/redirects.py` | `purchase_response` |
| `config/handoff.yaml` | Configuración de la derivación |
| `chat-view-test/admin.html`, `src/admin.{js,css}` | Panel |
| `chat-view-test/src/shared.js` | Markdown, fuentes y fallbacks compartidos con el chat |
| `chat-view-test/src/tokens.css` | Colores compartidos con el chat |

**Endpoints** (todos los `/admin/*` con el Bearer):

| Método y ruta | Para qué |
|---|---|
| `GET /admin/ping` | Validar la contraseña (login) |
| `GET /admin/stats` | Métricas |
| `GET /admin/conversations?status=&flow=&client_type=&q=&issues=&limit=&offset=` | Lista |
| `GET /admin/conversations/{id}` | Conversación con todos sus mensajes |
| `POST /admin/conversations/{id}/status` `{status: "human" \| "bot", author}` | Tomar / devolver o descartar |
| `POST /admin/conversations/{id}/messages` `{content, author}` | Responder (toma la conversación si hace falta) |
| `PUT /admin/messages/{id}/review` `{rating: "good" \| "bad" \| null, note, author}` | Revisar una respuesta |
| `GET /admin/documents` | Archivos de la KB con su visibilidad, más los tipos de cliente (`clients.yaml` + `common`) |
| `PUT /admin/documents/visibility` `{audience, source_id, client_types, author}` | Cambiar qué tipos consultan un archivo (422 si un tipo no existe) |
| `GET /chat/{id}/updates?after=<id>` (sin contraseña) | Polling del chat |

**Tests**: `tests/unit/test_handoff_gate.py` (patrones), `tests/e2e/test_graph_fakes.py` (derivación sin LLM y
seguridad primero), `tests/e2e/test_api_fakes.py` (contraseña y vista Archivos con un store falso) y
`tests/integration/test_conversations_pg.py` (ciclo completo contra Postgres:
`python -m uv run python -m pytest -m integration tests/integration/test_conversations_pg.py`).
`tests/integration/test_kb_documents_pg.py` cubre la visibilidad, la reutilización de embeddings y la migración
(⚠️ vacía `kb_chunks` y `kb_documents`).

**Limitaciones conocidas**:

- Lo que se habla con una persona no entra al historial del grafo: al devolver la conversación, el bot no lo
  recuerda.
- Si el cliente recarga el chat, empieza una sesión nueva (el `session_id` vive en memoria de la página).
- La contraseña es compartida y fácil a propósito; cambiarla antes de mostrar el panel fuera del equipo.

### Resetear conversaciones o la base

Ver [Resetear datos](../README.md#resetear-datos) en el README principal. En resumen: para limpiar conversaciones
usar `TRUNCATE`, que conserva la KB; `docker compose down -v` borra **todo**, incluida la KB ingerida.

## Aislamiento

- Tabla `kb_chunks` particionada `LIST (audience)`; cada store consulta directamente su partición.
- Roles `rag_support_ro` / `rag_sales_ro` (solo `SELECT` en su partición) con pool propio.
- **RLS** en cada partición: el rol RO solo ve filas con `client_types && app.client_types`, que el store fija en
  la transacción con `set_config(..., true)`. Sin la variable, 0 filas.
- Filtro tipado `RetrievalFilter` → `WHERE` parametrizado; assert post-recuperación descarta y registra violaciones.
- Contraseñas de `sql/001_init.sql` son de desarrollo: cámbialas en producción.

## Añadir un `client_type`

1. Añadirlo al `Literal` `ClientType` en `src/eco_kb/graph/state.py`.
2. Bloque nuevo en `config/clients.yaml` (tono, `cta`, `fallback`); sin él la app no arranca.
3. Tildarlo en el panel (Archivos) en los documentos que deba consultar. Opcional: carpetas
   `soporte/<ct>/` y `ventas/<ct>/` para que los archivos nuevos lo traigan como sugerencia.
4. Su etiqueta en `LABELS` de `src/eco_kb/kb_documents.py`.

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
