"""CLI: uv run python -m eco_kb.ingest.run"""
import logging
import sys
from collections import Counter
from pathlib import Path

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


def ingest(records: list[ChunkRecord], pool, embedder: Embedder,
           keep_sources: set[str] | None = None) -> dict[str, Counter]:
    """Upsert de `records`. `keep_sources` = fuentes que siguen existiendo aunque no vengan en `records`
    (p. ej. sin cambios o con error de lectura): sus chunks NO se borran. Por defecto, las de `records`."""
    stats = {"inserted": Counter(), "updated": Counter(), "unchanged": Counter(), "deleted": Counter()}
    rec_sources = {r.source_id for r in records}
    keep_sources = rec_sources if keep_sources is None else keep_sources | rec_sources
    with pool.connection() as conn:
        rows = conn.execute("SELECT audience, chunk_id, source_id, content_hash FROM kb_chunks").fetchall()
        existing = {(r["audience"], r["chunk_id"]): r["content_hash"] for r in rows}
        source_of = {(r["audience"], r["chunk_id"]): r["source_id"] for r in rows}
        todo = []
        for r in records:
            old = existing.get((r.audience, r.chunk_id))
            if old == r.content_hash:
                stats["unchanged"][r.audience] += 1
            else:
                todo.append(r)
                stats["updated" if old else "inserted"][r.audience] += 1

        if todo:
            vectors = embedder.embed_documents([r.content for r in todo])
            with conn.transaction():
                for r, v in zip(todo, vectors, strict=True):
                    row = r.model_dump() | {"embedding": "[" + ",".join(f"{x:.8f}" for x in v) + "]"}
                    conn.execute(_UPSERT, row)

        # Huérfanos: fuentes que ya no existen, o chunks sobrantes de una fuente re-procesada.
        new_ids = {(r.audience, r.chunk_id) for r in records}
        orphans = [
            k for k, src in source_of.items()
            if src not in keep_sources or (src in rec_sources and k not in new_ids)
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
    pool = make_pool(s.database_url_admin, "ingest", max_size=2)
    embedder = GeminiEmbedder(s.google_api_key, s.embedding_model, s.embedding_dim)
    stats = ingest(records, pool, embedder)
    for k, c in stats.items():
        log.info("%-9s support=%d sales=%d", k, c["support"], c["sales"])
    pool.close()
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
