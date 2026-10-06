"""La base de conocimiento se gestiona desde el panel (/admin → Archivos); esta tabla es su fuente de verdad.

`kb_documents` guarda por documento: audiencia, visibilidad (`client_types`), el archivo original, la versión
publicada (`markdown`, troceada y embebida en `kb_chunks`) y el borrador pendiente de revisión (`draft_markdown`).

- **Panel** (`origin = 'panel'`): los operadores suben el archivo; si ya tiene buen formato se publica directo, si
  no la IA arma un borrador que alguien revisa, edita y publica. Ver `DocumentStore`.
- **Archivos** (`origin = 'files'`, legado: `docs/estructurados` o Drive + `eco_kb.ingest.run`): la carpeta es la
  sugerencia inicial de visibilidad. La ingesta desde archivos nunca pisa ni borra un documento del panel; editar o
  reemplazar desde el panel un documento que vino de archivos lo pasa al panel.

La audiencia (soporte/ventas) es fija: sale de la carpeta o de lo que se elige al subir. `client_types` también se
copia a cada fila de `kb_chunks`, porque ahí la aplican el filtro del código y la RLS. Usa el pool admin: los roles
de solo lectura del RAG no ven esta tabla.
"""
from __future__ import annotations  # DocumentStore.list tapa al builtin en las anotaciones

import hashlib
import logging
import re
from pathlib import Path

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

log = logging.getLogger("eco_kb.kb_documents")

COMMON = "common"
LABELS = {COMMON: "Todos", "hotel": "Hotel", "bodega": "Bodega", "restaurante": "Restaurante", "generic": "Genérico"}
AUDIENCE_FOLDER = {"support": "soporte", "sales": "ventas"}
MAX_UPLOAD_BYTES = 15 * 2**20
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
    # v2: la KB se gestiona desde el panel (subir, revisar, editar, publicar). Lo existente queda como 'files'.
    2: """
    ALTER TABLE kb_documents
        ADD COLUMN IF NOT EXISTS origin          text NOT NULL DEFAULT 'files' CHECK (origin IN ('files', 'panel')),
        ADD COLUMN IF NOT EXISTS markdown        text,
        ADD COLUMN IF NOT EXISTS draft_markdown  text,
        ADD COLUMN IF NOT EXISTS review          jsonb,
        ADD COLUMN IF NOT EXISTS state           text NOT NULL DEFAULT 'ready'
                                                 CHECK (state IN ('processing', 'ready', 'error')),
        ADD COLUMN IF NOT EXISTS error           text,
        ADD COLUMN IF NOT EXISTS original        bytea,
        ADD COLUMN IF NOT EXISTS original_name   text,
        ADD COLUMN IF NOT EXISTS original_sha256 text,
        ADD COLUMN IF NOT EXISTS published_at    timestamptz;
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


def recover_interrupted(pool: ConnectionPool) -> int:
    """Al arrancar la API: un procesamiento cortado por un reinicio no puede quedar "procesando" para siempre."""
    with pool.connection() as conn:
        return conn.execute(
            "UPDATE kb_documents SET state = 'error', error = 'Se interrumpió el procesamiento (reinicio de la API). "
            "Volvé a subir el archivo.' WHERE state = 'processing'"
        ).rowcount


def normalize_client_types(types: list[str], valid: list[str]) -> list[str]:
    """Valida contra los tipos configurados. `common` incluye a todos, así que reemplaza al resto."""
    unknown = sorted(set(types) - set(valid) - {COMMON})
    if unknown:
        raise ValueError(f"tipo de cliente desconocido: {', '.join(unknown)}")
    if COMMON in types:
        return [COMMON]
    return [t for t in valid if t in types]  # orden estable (el de clients.yaml), sin duplicados


def sync_sources(conn, sources: dict[tuple[str, str], dict], keep_sources: set[str],
                 origin: str = "files") -> dict[tuple[str, str], list[str]]:
    """Para la ingesta. `sources` = {(audience, source_id): {title, product, client_types (sugerencia), markdown?}}.

    Inserta las fuentes nuevas con la sugerencia de la carpeta; de las existentes solo actualiza título, producto y
    (si viene de archivos) el markdown: la visibilidad la manda el panel. Con origin="files" además borra del
    manifiesto los archivos que ya no existen (nunca los gestionados desde el panel). Devuelve la visibilidad vigente."""
    out = {}
    for (audience, source_id), d in sources.items():
        row = conn.execute(
            """INSERT INTO kb_documents (audience, source_id, title, product, client_types, origin, markdown)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (audience, source_id) DO UPDATE SET title = EXCLUDED.title, product = EXCLUDED.product,
                   markdown = coalesce(EXCLUDED.markdown, kb_documents.markdown)
               RETURNING client_types""",
            (audience, source_id, d["title"], d["product"], d["client_types"], origin, d.get("markdown")),
        ).fetchone()
        out[(audience, source_id)] = row["client_types"]
    if origin == "files":
        conn.execute("DELETE FROM kb_documents WHERE origin = 'files' AND NOT (source_id = ANY(%s))",
                     (sorted(keep_sources),))
    return out


def panel_source_id(audience: str, filename: str) -> str:
    """source_id de un archivo subido desde el panel: <soporte|ventas>/panel/<nombre>.md (la carpeta `panel` no es
    un tipo de cliente: la visibilidad es la que se elige al subirlo o después)."""
    stem = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", " ", Path(filename).stem).strip(" .")[:150] or "documento"
    return f"{AUDIENCE_FOLDER[audience]}/panel/{stem}.md"


_DOC_COLS = """d.audience, d.source_id, d.title, d.product, d.client_types, d.updated_by, d.updated_at, d.created_at,
       d.origin, d.state, d.error, d.original_name, d.published_at, d.review -> 'alerts' AS alerts,
       coalesce((d.review ->> 'used_ai')::boolean, false) AS used_ai,
       d.draft_markdown IS NOT NULL AS has_draft, d.markdown IS NOT NULL AS has_markdown,
       d.original IS NOT NULL AS has_original, coalesce(c.chunks, 0) AS chunks"""
_DOC_SQL = f"""
SELECT {_DOC_COLS}
FROM kb_documents d
LEFT JOIN (SELECT audience, source_id, count(*) AS chunks FROM kb_chunks GROUP BY 1, 2) c
       USING (audience, source_id)
"""
_WHERE_DOC = " WHERE d.audience = %s AND d.source_id = %s"


class DocumentNotFound(Exception):
    pass


class DocumentStore:
    """El panel gestiona toda la KB: subir, procesar (IA solo si hace falta), revisar, editar, publicar y borrar.

    Estados de un documento (columnas de kb_documents):
      state = processing | ready | error   → el procesamiento del último archivo subido
      draft_markdown                        → borrador pendiente de revisión (el bot NO lo usa)
      markdown + chunks                     → versión publicada (lo que consulta el bot)
    Un archivo subido con buen formato se publica directo; uno que la IA tuvo que reorganizar queda en borrador.
    """

    def __init__(self, pool: ConnectionPool, client_types: list[str], embedder=None, ai=None):
        self.pool = pool
        self.client_types = list(client_types)  # los de clients.yaml
        self.embedder, self.ai = embedder, ai     # embedder: para publicar; ai: Restructurer (o None)

    def setup(self) -> list[int]:
        return setup(self.pool)

    def options(self) -> list[dict]:
        return [{"id": t, "label": LABELS.get(t, t.capitalize())} for t in (COMMON, *self.client_types)]

    def list(self) -> list[dict]:
        with self.pool.connection() as conn:
            return conn.execute(_DOC_SQL + " ORDER BY d.audience = 'sales', lower(d.title), d.source_id").fetchall()

    def _doc(self, conn, audience: str, source_id: str) -> dict | None:
        return conn.execute(_DOC_SQL + _WHERE_DOC, (audience, source_id)).fetchone()

    def get(self, audience: str, source_id: str) -> dict | None:
        """Con el contenido: markdown publicado, borrador y revisión (sin el archivo original)."""
        with self.pool.connection() as conn:
            doc = self._doc(conn, audience, source_id)
            if doc:
                doc |= conn.execute("SELECT markdown, draft_markdown, review FROM kb_documents d" + _WHERE_DOC,
                                    (audience, source_id)).fetchone()
            return doc

    def original(self, audience: str, source_id: str) -> tuple[str, bytes] | None:
        with self.pool.connection() as conn:
            row = conn.execute("SELECT original_name, original FROM kb_documents d" + _WHERE_DOC,
                               (audience, source_id)).fetchone()
        return (row["original_name"], bytes(row["original"])) if row and row["original"] is not None else None

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
            return self._doc(conn, audience, source_id)

    # --- subir y procesar -------------------------------------------------------------------------------------

    def upload(self, audience: str, filename: str, data: bytes, client_types: list[str], author: str | None,
               source_id: str | None = None) -> dict:
        """Guarda el original y deja el documento en `processing`. Hay que llamar a `process` después (en segundo
        plano). Con `source_id` reemplaza ese documento (nueva versión); si no, el nombre del archivo lo identifica:
        subir otra vez un archivo con el mismo nombre también lo reemplaza. Al reemplazar se conserva la
        visibilidad; en uno nuevo se usa la elegida al subirlo (vacía = sin habilitar)."""
        from eco_kb.ingest.restructure import SUPPORTED

        ext = Path(filename).suffix.lower()
        if ext not in SUPPORTED:
            raise ValueError(f"formato no soportado: {ext or filename} (se aceptan {', '.join(SUPPORTED)})")
        if not data:
            raise ValueError("el archivo está vacío")
        if len(data) > MAX_UPLOAD_BYTES:
            raise ValueError(f"el archivo supera {MAX_UPLOAD_BYTES // 2**20} MB")
        types = normalize_client_types(client_types, self.client_types)
        with self.pool.connection() as conn, conn.transaction():
            if source_id:
                exists = conn.execute("SELECT 1 FROM kb_documents d" + _WHERE_DOC, (audience, source_id)).fetchone()
                if not exists:
                    raise DocumentNotFound(source_id)
            sid = source_id or panel_source_id(audience, filename)
            conn.execute(
                """INSERT INTO kb_documents (audience, source_id, title, client_types, origin, state, original,
                                            original_name, original_sha256, updated_by)
                   VALUES (%(aud)s, %(sid)s, %(title)s, %(types)s, 'panel', 'processing', %(data)s, %(name)s,
                           %(sha)s, %(author)s)
                   ON CONFLICT (audience, source_id) DO UPDATE SET origin = 'panel', state = 'processing',
                       error = NULL, original = EXCLUDED.original, original_name = EXCLUDED.original_name,
                       original_sha256 = EXCLUDED.original_sha256, updated_by = EXCLUDED.updated_by,
                       updated_at = now()""",
                {"aud": audience, "sid": sid, "title": Path(filename).stem, "types": types, "data": data,
                 "name": Path(filename).name, "sha": hashlib.sha256(data).hexdigest(), "author": author},
            )
            return self._doc(conn, audience, sid)

    def process(self, audience: str, source_id: str, author: str | None = None) -> None:
        """Del original al borrador: sin IA si el formato ya es válido (y entonces se publica directo); con IA si
        no (y queda en borrador para revisar). Nunca lanza: los errores quedan en `state = 'error'`."""
        from eco_kb.ingest.restructure import NeedsAI, prepare, source_from_bytes, without_provenance

        try:
            name, data = self.original(audience, source_id) or (None, None)
            if data is None:
                raise DocumentNotFound(source_id)
            src = source_from_bytes(name, data)
            prep = prepare(src, AUDIENCE_FOLDER[audience], self.ai, set(self.client_types), Path(source_id))
            # Con buen formato se guarda tal cual vino; el borrador de la IA, sin los datos de procedencia.
            markdown = without_provenance(prep.markdown) if prep.used_ai else src.text.strip() + "\n"
            self._store_draft(audience, source_id, markdown, prep.review, author)
            if not prep.used_ai:
                self.publish(audience, source_id, author)
        except NeedsAI as exc:
            self._fail(audience, source_id, f"El archivo necesita que la IA lo reorganice ({exc}), pero no hay "
                                             "GOOGLE_API_KEY configurada.")
        except Exception as exc:  # el panel muestra el error; el resto de la KB no se toca
            log.exception("procesando %s", source_id)
            self._fail(audience, source_id, str(exc) or exc.__class__.__name__)

    def _fail(self, audience: str, source_id: str, error: str) -> None:
        with self.pool.connection() as conn:
            conn.execute("UPDATE kb_documents d SET state = 'error', error = %s, updated_at = now()" + _WHERE_DOC,
                         (error[:2000], audience, source_id))

    def _store_draft(self, audience: str, source_id: str, markdown: str, review: dict | None,
                     author: str | None) -> None:
        from eco_kb.ingest.loader import FrontMatter, parse_markdown

        raw, _ = parse_markdown(markdown)
        fm = FrontMatter(**{"title": Path(source_id).stem, "doc_type": "documento", **raw})
        with self.pool.connection() as conn:
            conn.execute(
                """UPDATE kb_documents d SET draft_markdown = %s, review = %s, state = 'ready', error = NULL,
                          title = CASE WHEN d.markdown IS NULL THEN %s ELSE d.title END,
                          product = CASE WHEN d.markdown IS NULL THEN %s ELSE d.product END,
                          updated_by = coalesce(%s, d.updated_by), updated_at = now()""" + _WHERE_DOC,
                (markdown, Jsonb(review) if review is not None else None, fm.title, fm.product, author,
                 audience, source_id),
            )

    # --- revisar, editar y publicar ---------------------------------------------------------------------------

    def save_draft(self, audience: str, source_id: str, markdown: str, author: str | None) -> dict:
        """Guarda una edición como borrador (el bot sigue usando la versión publicada hasta que se publique).
        Devuelve el chequeo de formato; falla (ValueError) solo si el documento no se podría cargar en la KB."""
        from eco_kb.ingest.format_check import check_format
        from eco_kb.ingest.loader import IngestError, load_text

        try:
            load_text(Path(source_id), markdown, set(self.client_types))
        except (IngestError, ValueError) as exc:
            raise ValueError(str(exc)) from exc
        with self.pool.connection() as conn:
            if not conn.execute("SELECT 1 FROM kb_documents d" + _WHERE_DOC, (audience, source_id)).fetchone():
                raise DocumentNotFound(source_id)
            review = conn.execute("SELECT review FROM kb_documents d" + _WHERE_DOC,
                                  (audience, source_id)).fetchone()["review"]
        self._store_draft(audience, source_id, markdown, review, author)
        fmt = check_format(markdown)
        return {"problems": fmt.problems, "warnings": fmt.warnings}

    def publish(self, audience: str, source_id: str, author: str | None) -> dict:
        """Publica el borrador (o vuelve a publicar la versión actual): troceado + embeddings solo de lo nuevo.
        A partir de acá el documento se gestiona desde el panel (la ingesta desde archivos ya no lo toca)."""
        from eco_kb.ingest.loader import load_text
        from eco_kb.ingest.run import ingest

        if self.embedder is None:
            raise RuntimeError("no hay embedder configurado para publicar")
        with self.pool.connection() as conn:
            row = conn.execute("SELECT coalesce(draft_markdown, markdown) AS md FROM kb_documents d" + _WHERE_DOC,
                               (audience, source_id)).fetchone()
        if row is None:
            raise DocumentNotFound(source_id)
        if not row["md"]:
            raise ValueError("no hay contenido para publicar (volvé a subir el archivo)")
        records = load_text(Path(source_id), row["md"], set(self.client_types))
        if not records:
            raise ValueError("el documento no tiene contenido")
        with self.pool.connection() as conn:  # pasa a gestionarse desde el panel ANTES de ingerir
            conn.execute("UPDATE kb_documents d SET origin = 'panel'" + _WHERE_DOC, (audience, source_id))
        stats = ingest(records, self.pool, self.embedder, origin="panel")
        with self.pool.connection() as conn:
            conn.execute(
                """UPDATE kb_documents d SET markdown = %s, draft_markdown = NULL, review = NULL, state = 'ready',
                          error = NULL, published_at = now(), updated_by = coalesce(%s, d.updated_by),
                          updated_at = now()""" + _WHERE_DOC,
                (row["md"], author, audience, source_id),
            )
            doc = self._doc(conn, audience, source_id)
        return {**doc, "stats": {k: sum(c.values()) for k, c in stats.items()}}

    def discard_draft(self, audience: str, source_id: str) -> dict | None:
        """Descarta el borrador. Si el documento nunca se publicó, lo borra entero. Devuelve el documento o None."""
        with self.pool.connection() as conn, conn.transaction():
            doc = self._doc(conn, audience, source_id)
            if doc is None:
                raise DocumentNotFound(source_id)
            if not doc["chunks"] and not doc["has_markdown"]:
                conn.execute("DELETE FROM kb_documents d" + _WHERE_DOC, (audience, source_id))
                return None
            conn.execute("UPDATE kb_documents d SET draft_markdown = NULL, review = NULL, state = 'ready', "
                         "error = NULL, updated_at = now()" + _WHERE_DOC, (audience, source_id))
            return self._doc(conn, audience, source_id)

    def delete(self, audience: str, source_id: str) -> None:
        """Borra el documento y sus fragmentos: el bot deja de consultarlo en el acto."""
        with self.pool.connection() as conn, conn.transaction():
            gone = conn.execute("DELETE FROM kb_documents d" + _WHERE_DOC + " RETURNING source_id",
                                (audience, source_id)).fetchone()
            if gone is None:
                raise DocumentNotFound(source_id)
            conn.execute("DELETE FROM kb_chunks WHERE audience = %s AND source_id = %s", (audience, source_id))
