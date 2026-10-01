# demo-eco

- `ECO-KB/` — API del chatbot (FastAPI + LangGraph + Postgres/pgvector). Ver su README.
- `chat-view-test/` — vista de chat de prueba (Vite). En Docker la sirve `vite preview`.

## Levantar ambas

```bash
cp .env.example .env     # opcional: red, contraseñas, puertos
docker compose up -d --build
```

- Vista: `http://<ip>:8080` (su servidor reenvía `/api/*` a la API, mismo origen, sin CORS; acepta cualquier `Host`,
  así que puedes ponerle delante tu propio proxy).
- API (solo depuración, localhost): `http://localhost:8000/docs`.
- No levanta Postgres: se une a la red `SHARED_NETWORK` (por defecto `servicios_compartidos`) donde ya vive, y lo
  ve como `DB_HOST` (por defecto `db`). La unión a la red es automática en cada `up`.
- La clave `GOOGLE_API_KEY` y los modelos salen de `ECO-KB/.env` (`ECO-KB/.env.server.example` es la plantilla).
- Base/usuarios/esquema en el Postgres compartido: `ECO-KB/scripts/bootstrap-db.sh` (una vez), y luego la ingesta
  de documentos (ver README de `ECO-KB`).

### En esta máquina de desarrollo (sin red compartida)

```bash
cd ECO-KB && docker compose -f compose.local-db.yml up -d && cd ..     # Postgres local
SHARED_NETWORK=eco-kb_default docker compose up -d --build
```

Parar: `docker compose down`. Logs: `docker compose logs -f api web`.
