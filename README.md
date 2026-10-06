# demo-eco

- `ECO-KB/` — API del chatbot (FastAPI + LangGraph + Postgres/pgvector). Ver su README.
- `chat-view-test/` — vista de chat de prueba (Vite). En Docker la sirve `vite preview`.

## Levantar ambas

```bash
cp .env.example .env     # opcional: red, contraseñas, puertos
docker compose up -d --build
```

- Vista: `http://<ip>:8088` (su servidor reenvía `/api/*` a la API, mismo origen, sin CORS; acepta cualquier `Host`,
  así que puedes ponerle delante tu propio proxy).
- Panel de administración (conversaciones, revisión de respuestas, derivaciones a una persona y qué tipos de
  cliente consultan cada archivo de la KB): `http://<ip>:8088/admin`. Contraseña: `admin` (se cambia con `ADMIN_TOKEN` en `ECO-KB/.env`).
- API (solo depuración, localhost): `http://localhost:8000/docs`.
- Levanta su propio Postgres con pgvector, el contenedor `eco-db` (sin puerto publicado; volumen `eco_db_data`).
  La primera vez crea la base `ecokb`, el admin `eco` y los roles de solo lectura (`ECO-KB/sql/init-db.sh`).
- La clave `GOOGLE_API_KEY` y los modelos salen de `ECO-KB/.env` (`ECO-KB/.env.server.example` es la plantilla).
- Tras el primer `up`, ingerir los documentos:

```bash
docker compose run --rm -e KB_DIR=docs/estructurados -v ./ECO-KB/docs:/app/docs:ro api python -m eco_kb.ingest.run
```

La API aplica sola la migración de la KB al arrancar (manifiesto `kb_documents` + RLS), sin re-embeber. Después de
ingerir, revisá en el panel → **Archivos** qué tipos de cliente consultan cada documento: un archivo nuevo arranca
con la visibilidad que sugiere su carpeta.

Consultar la base: `docker exec -it eco-db psql -U eco -d ecokb`.
Parar: `docker compose down` (con `-v` borra también la base). Logs: `docker compose logs -f api web db`.

## Panel de administración

`http://<ip>:8088/admin`, contraseña `admin` (se cambia con `ADMIN_TOKEN` en `ECO-KB/.env`). Sirve para revisar
las conversaciones, calificar las respuestas del bot, atender derivaciones a una persona y, en **Archivos**,
elegir qué tipos de cliente consultan cada documento (aplica al instante). Cómo se usa y cómo está implementado
(tablas, estados de la derivación, polling, endpoints):
[ECO-KB/README.md → Panel de administración](ECO-KB/README.md#panel-de-administración-y-derivación-a-humano).

## Resetear datos

La base (volumen `eco_db_data`) guarda tres cosas distintas:

| Qué | Tablas | Se recupera |
|---|---|---|
| Base de conocimiento ingerida (fragmentos, embeddings y la visibilidad elegida en el panel) | `kb_chunks*`, `kb_documents`, `kb_schema_migrations` | Volviendo a ingerir: los documentos fuente están en git (`ECO-KB/docs/estructurados`), pero recalcular los embeddings consume cuota de Gemini y la visibilidad hay que volver a elegirla en el panel |
| Memoria del bot por sesión (checkpointer de LangGraph) | `checkpoints`, `checkpoint_blobs`, `checkpoint_writes` | No hace falta: es historial de conversaciones |
| Conversaciones del panel, con calificaciones y notas | `chat_conversations`, `chat_messages` | No: se pierden |

**Limpiar las conversaciones y conservar la KB** (lo habitual; instantáneo, sin cuota y sin reiniciar nada):

```bash
docker exec eco-db psql -U eco -d ecokb -c "TRUNCATE chat_messages, chat_conversations, checkpoints, checkpoint_blobs, checkpoint_writes;"
```

Deja el panel vacío y borra la memoria del bot, así ninguna sesión vieja arrastra historial. No tocar
`checkpoint_migrations`. En local, con la base de `ECO-KB/compose.local-db.yml`:
`docker exec eco-kb-db-1 psql -U ecokb -d ecokb -c "TRUNCATE ..."` (mismas tablas).

**Empezar de cero** (borra **todo**, incluida la KB): solo si hace falta recrear la base, por ejemplo después de
cambiar las contraseñas `ECOKB_*` del `.env`, que solo se aplican al crear el volumen.

```bash
docker compose down -v
docker compose up -d --build
docker compose run --rm -e KB_DIR=docs/estructurados -v ./ECO-KB/docs:/app/docs:ro api python -m eco_kb.ingest.run
```
