# Contexto del proyecto: demo ECO360 (chatbot RAG B2B)

Documento de traspaso para retomar el proyecto y agregar features. Complementa a
`ECO-KB/AGENTE-DEBUG.md`, que es la guía detallada para depurar preguntas y respuestas.
Idioma de trabajo con el usuario: **español rioplatense**, claro y directo.

## 1. Qué es

Chatbot para una empresa de higiene profesional (sistema **ECO360**: OZONIFY, X3, X4, X5, SURFACE PROTECTANT,
BIO SANITIZER, Carro Ozonify), marca de **SecondSkin** (sitio: secondskin.com.ar). Es una **demo** para que la
prueben compañeras del equipo: se prioriza que sea simple, rápido de levantar y que "se vea profesional".

Dos flujos aislados, decididos SOLO por `is_registered` (nunca por el texto del mensaje):

- **support** (`is_registered=true`, en la vista "Cliente (dar soporte)"): responde con documentación técnica y cita fuentes.
- **sales** (`is_registered=false`, en la vista "No cliente (ventas)"): responde con documentación comercial y cierra
  siempre con un CTA. La URL del CTA la añade el código, nunca el LLM. **Nunca** da contenido técnico: dosis, diluciones, etc.

Tipos de cliente (`client_type`): `hotel | bodega | restaurante | generic`. Cada fragmento de la KB tiene
`client_types` derivado de la **ruta** del documento (nunca del contenido), y el filtro `client_types ∩ {ct, common}` es duro.

## 2. Repositorio y estructura

- GitHub: `MatiasStoroni/eco-soporte` (rama `master`). Copia local: `C:\Users\Follow\demo-eco`. Un solo repo
  (antes `ECO-KB` tenía un `.git` propio anidado; se eliminó y ahora son archivos normales).
- `compose.yml`: levanta los 3 contenedores de la demo (ver §4).
- `ECO-KB/`: API (Python 3.12, `uv`, FastAPI, LangGraph, Gemini, Postgres + pgvector).
  - `config/clients.yaml`: `domain` (descripción, glosario, `capabilities` por flujo) y, por `client_type`: tono, CTA y fallbacks.
  - `config/safety.yaml`: palabras clave y regex de seguridad + respuesta fija (sin LLM). Teléfonos de **Argentina**.
  - `src/eco_kb/graph/`: `builder.py`, `edges.py`, `prompts.py` (todos los prompts), `nodes/{session,safety,intent,retrieval_nodes,generation,finalize,common}.py`.
  - `src/eco_kb/retrieval/`: búsqueda híbrida (vector HNSW + FTS español, fusión RRF) y filtros.
  - `src/eco_kb/ingest/`: ingesta de Markdown (embebe solo lo cambiado, borra huérfanos). `src/eco_kb/drive/`: sync opcional con Google Drive.
  - `docs/estructurados/{soporte,ventas}/{hotel,comun,...}/*.md`: **la KB fuente que se ingesta**. (`kb/` es un ejemplo viejo, no se usa).
  - `docs/estructurados/LEEME - Pendientes y dudas.md`: brechas de contenido conocidas.
  - `sql/000_bootstrap.sql` (roles y base, parametrizado con `admin_user`), `sql/001_init.sql` (esquema particionado), `sql/init-db.sh` (init automático del contenedor).
  - `evals/questions.yaml` + `evals/run.py`: batería de evaluación con LLM real. `tests/`: unit + e2e con fakes (sin red) + integration.
  - `AGENTE-DEBUG.md`: pipeline, `audit_log`, diagnóstico por síntomas e invariantes. **Leerlo antes de tocar el RAG.**
- `chat-view-test/`: vista de chat (Vite, JS vanilla, sin framework). `src/main.js`, `src/style.css`, `index.html`.
  El servidor de Vite reenvía `/api/*` a la API (sin CORS). `API_URL` define el destino (por defecto `http://localhost:8000`).
- `.claude/launch.json`: configs `api` (uvicorn local) y `web` (Vite dev en :5173) para previsualizar en local.

## 3. Pipeline de un turno (resumen)

```
validate_session → safety_gate ─┬─ coincide → respuesta fija de seguridad (sin LLM)
                                └─ classify_intent ─┬─ business_question → support_rag | sales_rag
                                                    └─ greeting|smalltalk|capabilities|off_topic|unclear → converse
*_rag: rewrite_query (usa historial + glosario + catálogo de la KB) → retrieve → grade_documents → generate
       → check_grounding (citas + regex de cifras + LLM) → check_answer → finalize_support | finalize_sales (+CTA)
```

`POST /chat` con `{session_id, is_registered, client_type, language, message}` devuelve
`answer, flow, intent, sources[{title,section}], fallback_reason, cta_url, audit_log`. El historial se guarda en el servidor
por `session_id`. Modelos: `gemini-3.5-flash` (generar), `gemini-3.5-flash-lite` (clasificar, reescribir, evaluar),
embeddings `gemini-embedding-001` a 768 dimensiones.

## 4. Despliegue (servidor de desarrollo)

- Servidor: `/proyectos/eco-soporte-dev` (usuario `dev-user`). Se actualiza con `git pull` y
  `docker compose up -d --build` (hace falta `--build` siempre que cambie código o configuración: se copian a la imagen).
- Contenedores: `eco-db` (`pgvector/pgvector:pg18`, volumen `eco_db_data`, sin puerto publicado), `eco-demo-api-1`
  (`127.0.0.1:8000`), `eco-demo-web-1` (**puerto 8088** en el host; 8080 está ocupado en el servidor).
- Base: `ecokb`, admin `eco:eco`, roles de solo lectura `rag_support_ro` / `rag_sales_ro` (contraseñas por defecto
  `support_ro_dev` / `sales_ro_dev`). Se crean **solo la primera vez** (volumen vacío) con `sql/init-db.sh`.
  Si se cambian `ECOKB_*_PASSWORD` después → `password authentication failed`; solución para la demo:
  `docker compose down -v`, `up` y volver a ingerir.
- `demo-eco/.env` es opcional (puertos y contraseñas; ver `.env.example`). `ECO-KB/.env` es **obligatorio** en el servidor
  (`GOOGLE_API_KEY` y modelos; plantilla `ECO-KB/.env.server.example`). No está en git: **nunca imprimir ni commitear la clave**.
- Ingesta (tras el primer `up` o al cambiar documentos):
  `docker compose run --rm -e KB_DIR=docs/estructurados -v ./ECO-KB/docs:/app/docs:ro api python -m eco_kb.ingest.run`
- El Postgres compartido del servidor (`postgres-server`, red `servicios_internos`, superusuario `admin`) **no tiene
  pgvector** (probablemente Alpine) y **no se usa** para esta demo, por decisión explícita. Puede quedar basura de un
  bootstrap fallido en él (base `ecokb`, roles `eco`, `rag_*`); el comando para limpiarlo se le pasó al usuario.

## 5. Desarrollo local (Windows + Git Bash/PowerShell)

- Base local: `cd ECO-KB && docker compose -f compose.local-db.yml up -d` (contenedor `eco-kb-db-1`, `127.0.0.1:5433`, admin `ecokb:ecokb`).
- En `ECO-KB/.env` las URLs usan **`127.0.0.1`**, no `localhost`: `localhost` resuelve a IPv6 y Docker Desktop cuelga la
  conexión (el pool da timeout a los 15 s).
- Comandos (desde `ECO-KB/`): usar `python -m uv run python -m <módulo>`; el lanzador `uv run <exe>` falla en Windows
  ("uv trampoline failed").
  - Tests: `python -m uv run python -m pytest -q` (71 tests, sin red).
  - Evals: `python -m uv run python -m evals.run --flow sales|support --runs 3 [--only "texto"] [--show]`.
  - Ingesta local: `KB_DIR=docs/estructurados python -m uv run python -m eco_kb.ingest.run`.
  - Reproducir en proceso con `evals.run.build()` (ver `AGENTE-DEBUG.md` §3). Para varios turnos de la misma sesión, pasar
    `checkpointer=MemorySaver()` a `build_graph` y `configurable.thread_id`.
- En Git Bash, para `docker ... -v ./x:/app/x` usar `MSYS_NO_PATHCONV=1` (si no, la ingesta carga 0 documentos).
- `curl` rompe JSON con tildes: probar la API con Python (`urllib`).
- No nombrar scripts como módulos de la stdlib (`inspect.py` rompió los imports).

## 6. Estado actual (todo commiteado; último commit `d921a87 fix: emergency numbers`)

**KB ingerida:** support = 20 fragmentos `common` (fichas técnicas de X4, X5 y Carro Ozonify) + 28 `hotel` (manual operativo
de hotelería); sales = 7 fragmentos `common` (documento "ECO360 para hoteles premium - Análisis ejecutivo", movido a
`ventas/comun/` para que todos los tipos de cliente reciban información general).

**Resultados de evals (3 corridas):** ventas 105/105; soporte 168/168.

**Cambios hechos en esta etapa:**
- Ventas para tipos de cliente sin material propio: el documento comercial pasó a común. El generador presenta el material
  de otro sector como beneficio general de ECO360 ("documentado en hotelería") sin trasladarlo al rubro del cliente.
  El tono de bodega ya no fuerza vocabulario enológico, que provocaba frases inventadas. El verificador recibe el título y la
  sección de cada fragmento. Las `capabilities` de ventas son genéricas, no hablan de hotel.
- `evals/run.py`: soporta `client_type` por caso y `--flow`. Casos nuevos para restaurante, bodega y genérico, y de seguridad (lavandina).
- Seguridad: teléfonos de Argentina (107 emergencias médicas, 911, Centro Nacional de Intoxicaciones 0800-333-0160).
  Palabras clave nuevas: `lavandina`, `tragué/trague`. Ojo: no agregar "cloro" ("x4 tiene cloro?" debe responder).
  El usuario debe confirmar los números con el responsable de seguridad de la empresa.
- Vista rediseñada con la paleta de secondskin.com.ar: amarillo `#FFD70A`, aqua `#7EBEC5`, gris violáceo `#514F5B`, Poppins.
  Lleva tarjeta redondeada, avatar del bot, campo de texto tipo píldora, CTA como botón amarillo "Hablar con el equipo comercial"
  (la URL se quita del texto) y fuentes agrupadas por documento en un desplegable cerrado. El switch dice "Cliente (dar soporte)"
  o "No cliente (ventas)". El tipo de cliente por defecto es **Genérico**. Las sugerencias se ven en dos filas en desktop y en
  una fila deslizable en móvil. Mantiene modo oscuro.

## 7. Pendientes y cosas a saber

- **Contenido:** no hay material comercial propio de restaurante, bodega ni genérico (solo el de hoteles, ahora común), ni
  documento de ventas por producto (X4, X5, Ozonify). Tampoco hay dosificación de X3 (ver `LEEME`). **No inventar contenido**:
  si falta, reportarlo; lo completan los operadores. El contenido nuevo va en `docs/estructurados/ventas/<tipo>/` o `comun/`
  y requiere volver a ingerir (y moverlo también en Drive si se usa el sync).
- **Sugerencia "¿Qué dosis de X5 recomiendan?"** de la vista: está en modo ventas **a propósito** (demuestra que ventas no da
  datos técnicos), pero en la demo parece una falla. Se le ofreció al usuario pasarla a modo cliente o cambiarle el texto; sin decidir.
- Las sugerencias de la vista cambian el tipo de cliente a **Hotel** al tocarlas (es el único con material propio).
- **Verificador estricto:** a veces rechaza el primer borrador por una paráfrasis (`llm_ungrounded`) y el reintento lo
  corrige. Funciona, pero suma latencia (respuestas de 10 a 25 s). No relajar el verificador para "arreglarlo".
- El sitio secondskin.com.ar tiene el **certificado HTTPS vencido** (el navegador integrado no lo abre).

## 8. Reglas de trabajo (acordadas)

1. Reproducir primero, con el `audit_log`, al menos 3 veces (los LLM no son deterministas). Un caso que falla 1 de 3 veces es un problema real.
2. Cambiar **una palanca a la vez**, en este orden: configuración (`clients.yaml`, glosario, `safety.yaml`) o contenido de la KB →
   prompts → código (último recurso). Medir antes y después con evals `--runs 3` y `pytest`; si algo empeora, revertir.
3. Todo caso que falle se agrega a `evals/questions.yaml` antes de arreglarlo.
4. Respetar los invariantes de `AGENTE-DEBUG.md` §6: campos protegidos solo en `validate_session`, aislamiento entre ventas y
   soporte (particiones + roles de solo lectura + filtro), CTA solo por código, responder solo con lo que dicen los fragmentos,
   seguridad primero y sin LLM.
5. Cuidar la cuota de Gemini (evals completas = cientos de llamadas).
6. Al terminar: causa raíz con evidencia, cambio aplicado, evals antes y después, y riesgos o pendientes. Recordar al usuario
   que el servidor necesita `git pull` + `docker compose up -d --build` (y volver a ingerir si cambiaron documentos).
7. Commit y push los hace el usuario (el modo automático bloqueó `git commit/push` del agente).

## 9. Panel de administración y derivación a humano

- Vista `chat-view-test/admin.html` (`/admin` en la vista): lista de conversaciones con filtros, traza legible del pipeline por respuesta, calificación 👍/👎 con nota, "Copiar caso de eval" (formato de `evals/questions.yaml`) y respuesta como persona del equipo. Acceso con `ADMIN_TOKEN` (en `ECO-KB/.env`; vacío = panel desactivado) más el nombre de quien revisa.
- Registro: tablas `chat_conversations` y `chat_messages` (`src/eco_kb/conversations.py`), creadas por la API al arrancar. Usa el pool admin; los roles de solo lectura no las ven. Si el registro falla, el chat sigue.
- Derivación como último recurso (`config/handoff.yaml`): pedido explícito, detectado en `handoff_gate` (determinista, después de seguridad, sin LLM), o N fallbacks seguidos (por defecto 3). Estados `bot → pending` (el bot sigue respondiendo) `→ human` (alguien la tomó: el bot no responde y la vista consulta `GET /chat/{id}/updates`) `→ bot`.
- Limitaciones: los mensajes intercambiados con una persona no entran al historial del grafo; la vista del chat pierde la sesión si se recarga; el token es compartido (no hay usuarios reales).
