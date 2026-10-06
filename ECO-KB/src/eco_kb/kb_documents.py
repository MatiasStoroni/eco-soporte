"""Manifiesto de documentos de la KB: qué tipos de cliente consultan cada archivo.

La visibilidad la edita el equipo desde el panel (/admin → Archivos) y se guarda en `kb_documents`. La carpeta del
archivo solo es la sugerencia inicial cuando aparece por primera vez (`hotel/` → hotel, `comun/` → todos, cualquier
otra → sin habilitar); después la ingesta nunca la pisa. La audiencia (soporte/ventas) sí sale siempre de la carpeta.

`client_types` también se copia a cada fila de `kb_chunks`, porque ahí la aplican el filtro del código y la RLS.
Usa el pool admin: los roles de solo lectura del RAG no ven esta tabla.
"""
from __future__ import annotations  # DocumentStore.list tapa al builtin en las anotaciones

from psycopg_pool import ConnectionPool

COMMON = "common"
LABELS = {COMMON: "Todos", "hotel": "Hotel", "bodega": "Bodega", "restaurante": "Restaurante", "generic": "Genérico"}
_LOCK_ID = 727_002  # advisory lock de la migración (la API y la ingesta pueden arrancar a la vez)

# Cada versión se aplica una sola vez, en orden, y queda registrada en kb_schema_migrations.
MIGRATIONS: dict[int, str] = {
    1: """
    CREATE TABLE IF NOT EXISTS kb_documents (
        audience     text        NOT NULL CHECK (audience IN ('support', 'sales')),
        source_id    text        NOT NULL,
        title        text        NOT NULL,
        product      text        NOT NULL DEFAULT '',
        client_types text[]      NOT NULL DEFAULT '{}',
        updated_by   text,
        updated_at   timestamptz NOT NULL DEFAULT now(),
        created_at   timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (audience, source_id)
    );
    REVOKE ALL ON kb_documents FROM PUBLIC;

    -- client_types = '{}' significa "sin habilitar": ningún cliente lo consulta.
    ALTER TABLE kb_chunks DROP CONSTRAINT IF EXISTS kb_chunks_client_types_check;

    -- Sembrar el manifiesto con lo que ya estaba ingerido (la visibilidad que daba la carpeta).
    INSERT INTO kb_documents (audience, source_id, title, product, client_types)
    SELECT DISTINCT ON (audience, source_id) audience, source_id, title, product, client_types
    FROM kb_chunks ORDER BY audience, source_id, chunk_id
    ON CONFLICT DO NOTHING;

    -- El hash pasa a ser solo del contenido (igual a ChunkRecord.make_hash): así no se re-embebe nada.
    UPDATE kb_chunks SET content_hash = encode(sha256(convert_to(content, 'UTF8')), 'hex');

    -- RLS: la base filtra por tipo de cliente aunque el código omita el WHERE. Sin app.client_types, 0 filas.
    -- El admin es dueño de las tablas y no está sujeto a RLS (la ingesta y el panel lo ven todo).
    ALTER TABLE kb_chunks_support ENABLE ROW LEVEL SECURITY;
    ALTER TABLE kb_chunks_sales ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS kb_client_types ON kb_chunks_support;
    DROP POLICY IF EXISTS kb_client_types ON kb_chunks_sales;
    CREATE POLICY kb_client_types ON kb_chunks_support FOR SELECT TO rag_support_ro
        USING (client_types && string_to_array(current_setting('app.client_types', true), ','));
    CREATE POLICY kb_client_types ON kb_chunks_sales FOR SELECT TO rag_sales_ro
        USING (client_types && string_to_array(current_setting('app.client_types', true), ','));
    """,
}


def setup(pool: ConnectionPool) -> list[int]:
    """Idempotente: aplica las migraciones pendientes. Devuelve las versiones aplicadas ahora."""
    applied = []
    with pool.connection() as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_ID,))
        conn.execute("CREATE TABLE IF NOT EXISTS kb_schema_migrations ("
                     "version int PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
        done = {r["version"] for r in conn.execute("SELECT version FROM kb_schema_migrations").fetchall()}
        for version, ddl in sorted(MIGRATIONS.items()):
            if version in done:
                continue
            conn.execute(ddl)
            conn.execute("INSERT INTO kb_schema_migrations (version) VALUES (%s)", (version,))
            applied.append(version)
    return applied


def normalize_client_types(types: list[str], valid: list[str]) -> list[str]:
    """Valida contra los tipos configurados. `common` incluye a todos, así que reemplaza al resto."""
    unknown = sorted(set(types) - set(valid) - {COMMON})
    if unknown:
        raise ValueError(f"tipo de cliente desconocido: {', '.join(unknown)}")
    if COMMON in types:
        return [COMMON]
    return [t for t in valid if t in types]  # orden estable (el de clients.yaml), sin duplicados


def sync_sources(conn, sources: dict[tuple[str, str], dict], keep_sources: set[str]) -> dict[tuple[str, str], list[str]]:
    """Para la ingesta. `sources` = {(audience, source_id): {title, product, client_types (sugerencia)}}.

    Inserta las fuentes nuevas con la sugerencia de la carpeta; de las existentes solo actualiza título y producto
    (la visibilidad la manda el panel). Borra las que ya no existen. Devuelve la visibilidad vigente de `sources`."""
    out = {}
    for (audience, source_id), d in sources.items():
        row = conn.execute(
            """INSERT INTO kb_documents (audience, source_id, title, product, client_types)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (audience, source_id) DO UPDATE SET title = EXCLUDED.title, product = EXCLUDED.product
               RETURNING client_types""",
            (audience, source_id, d["title"], d["product"], d["client_types"]),
        ).fetchone()
        out[(audience, source_id)] = row["client_types"]
    conn.execute("DELETE FROM kb_documents WHERE NOT (source_id = ANY(%s))", (sorted(keep_sources),))
    return out


_DOC_SQL = """
SELECT d.audience, d.source_id, d.title, d.product, d.client_types, d.updated_by, d.updated_at, d.created_at,
       coalesce(c.chunks, 0) AS chunks
FROM kb_documents d
LEFT JOIN (SELECT audience, source_id, count(*) AS chunks FROM kb_chunks GROUP BY 1, 2) c
       USING (audience, source_id)
"""


class DocumentStore:
    """Lo que usa el panel: listar documentos y cambiar su visibilidad (sin re-embeber)."""

    def __init__(self, pool: ConnectionPool, client_types: list[str]):
        self.pool = pool
        self.client_types = list(client_types)  # los de clients.yaml

    def setup(self) -> list[int]:
        return setup(self.pool)

    def options(self) -> list[dict]:
        return [{"id": t, "label": LABELS.get(t, t.capitalize())} for t in (COMMON, *self.client_types)]

    def list(self) -> list[dict]:
        with self.pool.connection() as conn:
            return conn.execute(_DOC_SQL + " ORDER BY d.audience = 'sales', lower(d.title), d.source_id").fetchall()

    def set_visibility(self, audience: str, source_id: str, client_types: list[str], author: str | None) -> dict | None:
        """Cambia la visibilidad en el manifiesto y en los fragmentos, en una transacción. No embebe nada."""
        types = normalize_client_types(client_types, self.client_types)
        with self.pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE kb_documents SET client_types = %s, updated_by = %s, updated_at = now()
                   WHERE audience = %s AND source_id = %s RETURNING source_id""",
                (types, author, audience, source_id),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE kb_chunks SET client_types = %s, updated_at = now() WHERE audience = %s AND source_id = %s",
                (types, audience, source_id),
            )
            return conn.execute(_DOC_SQL + " WHERE d.audience = %s AND d.source_id = %s",
                                (audience, source_id)).fetchone()
