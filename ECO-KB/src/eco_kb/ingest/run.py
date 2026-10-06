"""CLI: uv run python -m eco_kb.ingest.run"""
import logging
import sys
from collections import Counter
from pathlib import Path

from eco_kb import kb_documents
from eco_kb.config import load_config
from eco_kb.db import make_pool
from eco_kb.ingest.loader import ChunkRecord, load_kb
from eco_kb.llm import Embedder, GeminiEmbedder
from eco_kb.settings import get_settings

log = logging.getLogger("eco_kb.ingest")

_UPSERT = """
INSERT INTO kb_chunks (chunk_id, audience, source_id, title, section_path, content, client_types,
                       doc_type, product, language, status, content_hash, embedding, updated_at)
VALUES (%(chunk_id)s, %(audience)s, %(source_id)s, %(title)s, %(section_path)s, %(content)s,
        %(client_types)s, %(doc_type)s, %(product)s, %(language)s, %(status)s, %(content_hash)s,
        %(embedding)s::vector, now())
ON CONFLICT (audience, chunk_id) DO UPDATE SET
  source_id = EXCLUDED.source_id, title = EXCLUDED.title, section_path = EXCLUDED.section_path,
  content = EXCLUDED.content, client_types = EXCLUDED.client_types, doc_type = EXCLUDED.doc_type,
  product = EXCLUDED.product, language = EXCLUDED.language, status = EXCLUDED.status,
  content_hash = EXCLUDED.content_hash, embedding = EXCLUDED.embedding, updated_at = now()
"""


_META_FIELDS = ("source_id", "title", "section_path", "client_types", "doc_type", "product", "language", "status")

_UPDATE_META = """
UPDATE kb_chunks SET source_id = %(source_id)s, title = %(title)s, section_path = %(section_path)s,
  client_types = %(client_types)s, doc_type = %(doc_type)s, product = %(product)s, language = %(language)s,
  status = %(status)s, updated_at = now()
WHERE audience = %(audience)s AND chunk_id = %(chunk_id)s
"""

STAT_KEYS = ("embedded", "reused", "meta_only", "unchanged", "deleted", "panel")


def _vec_literal(v) -> str:
    return "[" + ",".join(f"{x:.8f}" for x in v) + "]"


def ingest(records: list[ChunkRecord], pool, embedder: Embedder, keep_sources: set[str] | None = None,
           texts: dict[str, str] | None = None, origin: str = "files") -> dict[str, Counter]:
    """Upsert de `records`. `keep_sources` = fuentes que siguen existiendo aunque no vengan en `records`
    (p. ej. sin cambios o con error de lectura): sus chunks NO se borran. Por defecto, las de `records`.

    origin="files" (repo/Drive, la KB completa): los documentos gestionados desde el panel se saltean (stat
    `panel`) y nunca se borran. `texts` = {source_id: markdown} para poder editarlos después en el panel.
    origin="panel" (publicar un documento desde el panel): solo se tocan las fuentes de `records`.

    La visibilidad (client_types) sale del manifiesto `kb_documents`: los de `records` son solo la sugerencia de
    la carpeta para las fuentes nuevas. Solo se embebe un contenido (content_hash) que no esté ya en la base.
    Estadísticas: embedded (llamó al embedder), reused (embedding de otra fila con el mismo contenido),
    meta_only (mismo contenido, cambió algún metadato), unchanged, deleted, panel (salteados)."""
    kb_documents.setup(pool)  # por si la ingesta corre antes que la API
    stats = {k: Counter() for k in STAT_KEYS}
    with pool.connection() as conn:
        rows = conn.execute(
            f"SELECT audience, chunk_id, content_hash, {', '.join(_META_FIELDS)} FROM kb_chunks"
        ).fetchall()
        existing = {(r["audience"], r["chunk_id"]): r for r in rows}
        if origin == "panel":
            keep_sources = {r["source_id"] for r in rows}
        else:
            panel = {r["source_id"] for r in conn.execute(
                "SELECT source_id FROM kb_documents WHERE origin = 'panel'").fetchall()}
            for r in records:
                if r.source_id in panel:
                    stats["panel"][r.audience] += 1
            records = [r for r in records if r.source_id not in panel]
            keep_sources = (keep_sources or set()) | panel
        rec_sources = {r.source_id for r in records}
        keep_sources = (keep_sources or set()) | rec_sources

        with conn.transaction():
            sources = {(r.audience, r.source_id): {"title": r.title, "product": r.product,
                                                   "client_types": r.client_types,
                                                   "markdown": (texts or {}).get(r.source_id)} for r in records}
            visibility = kb_documents.sync_sources(conn, sources, keep_sources, origin)
        for r in records:
            r.client_types = visibility[(r.audience, r.source_id)]
        pending, meta_only = [], []
        for r in records:
            old = existing.get((r.audience, r.chunk_id))
            if old and old["content_hash"] == r.content_hash:
                if all(old[k] == getattr(r, k) for k in _META_FIELDS):
                    stats["unchanged"][r.audience] += 1
                else:
                    meta_only.append(r)
                    stats["meta_only"][r.audience] += 1
            else:
                pending.append(r)

        # Embeddings ya guardados con el mismo contenido (archivo movido, duplicado o en otra audiencia).
        known: dict[str, str] = {}
        if pending:
            known = {row["content_hash"]: row["embedding"] for row in conn.execute(
                "SELECT DISTINCT ON (content_hash) content_hash, embedding::text AS embedding FROM kb_chunks "
                "WHERE content_hash = ANY(%s)", (sorted({r.content_hash for r in pending}),),
            ).fetchall()}
        to_embed: dict[str, str] = {}  # hash -> contenido; un mismo texto se embebe una vez
        for r in pending:
            if r.content_hash in known or r.content_hash in to_embed:
                stats["reused"][r.audience] += 1
            else:
                to_embed[r.content_hash] = r.content
                stats["embedded"][r.audience] += 1
        if to_embed:
            vectors = embedder.embed_documents(list(to_embed.values()))
            known |= {h: _vec_literal(v) for h, v in zip(to_embed, vectors, strict=True)}

        if pending or meta_only:
            with conn.transaction():
                for r in pending:
                    conn.execute(_UPSERT, r.model_dump() | {"embedding": known[r.content_hash]})
                for r in meta_only:
                    conn.execute(_UPDATE_META, r.model_dump())

        # Huérfanos: fuentes que ya no existen, o chunks sobrantes de una fuente re-procesada.
        new_ids = {(r.audience, r.chunk_id) for r in records}
        orphans = [
            k for k, row in existing.items()
            if row["source_id"] not in keep_sources or (row["source_id"] in rec_sources and k not in new_ids)
        ]
        with conn.transaction():
            for audience, chunk_id in orphans:
                conn.execute("DELETE FROM kb_chunks WHERE audience = %s AND chunk_id = %s", (audience, chunk_id))
                stats["deleted"][audience] += 1
    return stats


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    s = get_settings()
    cfg = load_config(s.clients_path, s.safety_path)
    records, errors = load_kb(Path(s.kb_dir), set(cfg.clients))
    for e in errors:
        log.error("RECHAZADO %s", e)
    kb = Path(s.kb_dir)
    texts = {p.relative_to(kb).as_posix(): p.read_text(encoding="utf-8")
             for p in kb.rglob("*.md") if p.parent != kb}  # para editarlos después desde el panel
    pool = make_pool(s.database_url_admin, "ingest", max_size=2)
    embedder = GeminiEmbedder(s.google_api_key, s.embedding_model, s.embedding_dim)
    stats = ingest(records, pool, embedder, texts=texts)
    for k, c in stats.items():
        log.info("%-10s support=%d sales=%d", k, c["support"], c["sales"])
    if sum(stats["panel"].values()):
        log.info("(panel = fragmentos de archivos que ahora se gestionan desde el panel: la ingesta no los toca)")
    pool.close()
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
