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

La API aplica sola las migraciones de la KB al arrancar, sin re-embeber. Esa ingesta inicial carga los documentos
del repo una sola vez. **Desde ahí, la base de conocimiento se gestiona entera desde el panel → Archivos**: los
operadores suben los archivos y los revisan y publican ahí, sin tocar el repo ni redesplegar.

Consultar la base: `docker exec -it eco-db psql -U eco -d ecokb`.
Parar: `docker compose down` (con `-v` borra también la base). Logs: `docker compose logs -f api web db`.

## Panel de administración

`http://<ip>:8088/admin`, contraseña `admin` (se cambia con `ADMIN_TOKEN` en `ECO-KB/.env`). Sirve para revisar
las conversaciones, calificar las respuestas del bot y atender derivaciones a una persona. En **Archivos** se
gestiona la base de conocimiento:
- subir archivos (PDF, Word o Markdown): los que tienen buen formato se publican directo; los demás los reorganiza
  la IA en un borrador para revisar;
- editar y publicar;
- elegir qué tipos de cliente consultan cada documento (aplica al instante).

Cómo se usa y cómo está implementado (tablas, estados de la derivación, polling, endpoints):
[ECO-KB/README.md → Panel de administración](ECO-KB/README.md#panel-de-administración-y-derivación-a-humano).

## Resetear datos

La base (volumen `eco_db_data`) guarda tres cosas distintas. **Los documentos subidos desde el panel solo existen
ahí**, así que conviene respaldarla de vez en cuando y siempre antes de cualquier reset:

```bash
docker exec eco-db pg_dump -U eco -d ecokb -Fc -f /tmp/ecokb.dump && docker cp eco-db:/tmp/ecokb.dump ./ecokb-$(date +%F).dump
```

Para restaurarla: `docker cp` del archivo al contenedor + `pg_restore -U eco -d ecokb --clean --if-exists`. Los 2
errores de constraints heredadas son esperables.

| Qué | Tablas | Se recupera |
|---|---|---|
| Base de conocimiento: documentos (originales, versiones publicadas y borradores), visibilidad, fragmentos y embeddings | `kb_documents`, `kb_chunks*`, `kb_schema_migrations` | **Solo con un respaldo.** Sin respaldo, únicamente los documentos del repo (`ECO-KB/docs/estructurados`), volviendo a ingerir (gasta cuota); los subidos desde el panel se pierden |
| Memoria del bot por sesión (checkpointer de LangGraph) | `checkpoints`, `checkpoint_blobs`, `checkpoint_writes` | No hace falta: es historial de conversaciones |
| Conversaciones del panel, con calificaciones y notas | `chat_conversations`, `chat_messages` | No: se pierden |

**Limpiar las conversaciones y conservar la KB** (lo habitual; instantáneo, sin cuota y sin reiniciar nada):

```bash
docker exec eco-db psql -U eco -d ecokb -c "TRUNCATE chat_messages, chat_conversations, checkpoints, checkpoint_blobs, checkpoint_writes;"
```

Deja el panel vacío y borra la memoria del bot, así ninguna sesión vieja arrastra historial. No tocar
`checkpoint_migrations`. En local, con la base de `ECO-KB/compose.local-db.yml`:
`docker exec eco-kb-db-1 psql -U ecokb -d ecokb -c "TRUNCATE ..."` (mismas tablas).

**Empezar de cero** (borra **todo**, incluida la KB y los documentos subidos desde el panel: respaldá antes): solo si
hace falta recrear la base, por ejemplo después de cambiar las contraseñas `ECOKB_*` del `.env`, que solo se aplican
al crear el volumen.

```bash
docker compose down -v
docker compose up -d --build
docker compose run --rm -e KB_DIR=docs/estructurados -v ./ECO-KB/docs:/app/docs:ro api python -m eco_kb.ingest.run
```
