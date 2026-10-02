# demo-eco

- `ECO-KB/` — API del chatbot (FastAPI + LangGraph + Postgres/pgvector). Ver su README.
- `chat-view-test/` — vista de chat de prueba (Vite). En Docker la sirve `vite preview`.

## Levantar ambas

```bash
cp .env.example .env     # opcional: red, contraseñas, puertos
docker compose up -d --build
```

- Vista: `http://<ip>:8090` (su servidor reenvía `/api/*` a la API, mismo origen, sin CORS; acepta cualquier `Host`,
  así que puedes ponerle delante tu propio proxy).
- API (solo depuración, localhost): `http://localhost:8000/docs`.
- Levanta su propio Postgres con pgvector, el contenedor `eco-db` (sin puerto publicado; volumen `eco_db_data`).
  La primera vez crea la base `ecokb`, el admin `eco` y los roles de solo lectura (`ECO-KB/sql/init-db.sh`).
- La clave `GOOGLE_API_KEY` y los modelos salen de `ECO-KB/.env` (`ECO-KB/.env.server.example` es la plantilla).
- Tras el primer `up`, ingerir los documentos:

```bash
docker compose run --rm -e KB_DIR=docs/estructurados -v ./ECO-KB/docs:/app/docs:ro api python -m eco_kb.ingest.run
```

Consultar la base: `docker exec -it eco-db psql -U eco -d ecokb`.
Parar: `docker compose down` (con `-v` borra también la base). Logs: `docker compose logs -f api web db`.
