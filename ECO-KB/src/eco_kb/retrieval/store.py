import logging
import time
from typing import Protocol

from psycopg import sql
from psycopg_pool import ConnectionPool

from eco_kb.retrieval.filters import RetrievalFilter, violates_filter

log = logging.getLogger("eco_kb.security")

# Allowlist interna: el nombre de tabla NUNCA viene de input.
_TABLES = {"support": "kb_chunks_support", "sales": "kb_chunks_sales"}
_COLS = "chunk_id, audience, source_id, title, section_path, content, client_types"
RRF_K = 60


class ChunkStore(Protocol):
    def search(self, query_vec: list[float], query_text: str, f: RetrievalFilter) -> list[dict]: ...
    def catalog(self, f: RetrievalFilter) -> list[dict]: ...  # [{title, product, sections: [..]}]


def _vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.8f}" for x in vec) + "]"


def rrf_fuse(vector_rows: list[dict], fts_rows: list[dict], top_k: int) -> list[dict]:
    fused: dict[str, dict] = {}
    for rows, key in ((vector_rows, "vec"), (fts_rows, "fts")):
        for rank, row in enumerate(rows, start=1):
            item = fused.setdefault(
                row["chunk_id"], {**row, "vec_score": None, "rrf": 0.0}
            )
            item["rrf"] += 1.0 / (RRF_K + rank)
            if key == "vec":
                item["vec_score"] = float(row["score"])
    for item in fused.values():
        item.pop("score", None)
    return sorted(fused.values(), key=lambda r: r["rrf"], reverse=True)[:top_k]


class PgChunkStore:
    """Consulta DIRECTAMENTE su partición (nunca la tabla padre), con el pool de su rol RO."""

    def __init__(self, audience: str, pool: ConnectionPool, top_k: int = 8, candidate_k: int = 20):
        if audience not in _TABLES:
            raise ValueError(audience)
        self.audience, self._pool = audience, pool
        self._top_k, self._cand = top_k, candidate_k
        self._table = sql.Identifier(_TABLES[audience])
        self._catalog_cache: dict[tuple, tuple[float, list[dict]]] = {}

    def search(self, query_vec: list[float], query_text: str, f: RetrievalFilter) -> list[dict]:
        if f.audience != self.audience:
            raise PermissionError(f"store {self.audience} usado con filtro {f.audience}")
        params = {
            "types": list(f.allowed_client_types), "status": f.status, "lang": f.language,
            "vec": _vec_literal(query_vec), "q": query_text, "n": self._cand,
        }
        where = sql.SQL("client_types && %(types)s::text[] AND status = %(status)s AND language = %(lang)s")
        vec_q = sql.SQL(
            "SELECT {cols}, 1 - (embedding <=> %(vec)s::vector) AS score FROM {t} WHERE {w} "
            "ORDER BY embedding <=> %(vec)s::vector LIMIT %(n)s"
        ).format(cols=sql.SQL(_COLS), t=self._table, w=where)
        fts_q = sql.SQL(
            "SELECT {cols}, ts_rank_cd(tsv, websearch_to_tsquery('spanish', %(q)s)) AS score FROM {t} "
            "WHERE {w} AND tsv @@ websearch_to_tsquery('spanish', %(q)s) ORDER BY score DESC LIMIT %(n)s"
        ).format(cols=sql.SQL(_COLS), t=self._table, w=where)
        with self._pool.connection() as conn:
            vec_rows = conn.execute(vec_q, params).fetchall()
            fts_rows = conn.execute(fts_q, params).fetchall()

        chunks = rrf_fuse(vec_rows, fts_rows, self._top_k)
        ok = [c for c in chunks if not violates_filter(c, f)]
        if len(ok) != len(chunks):
            log.error("SECURITY: %d chunks violaron el filtro en %s", len(chunks) - len(ok), self.audience)
        return ok

    CATALOG_TTL = 300.0

    def catalog(self, f: RetrievalFilter) -> list[dict]:
        """Títulos, productos y secciones visibles con este filtro (para que el reescritor use el vocabulario real)."""
        if f.audience != self.audience:
            raise PermissionError(f"store {self.audience} usado con filtro {f.audience}")
        key = (f.allowed_client_types, f.status, f.language)
        cached = self._catalog_cache.get(key)
        if cached and time.monotonic() - cached[0] < self.CATALOG_TTL:
            return cached[1]
        q = sql.SQL(
            "SELECT title, product, section_path FROM {t} "
            "WHERE client_types && %(types)s::text[] AND status = %(status)s AND language = %(lang)s "
            "ORDER BY title, chunk_id"
        ).format(t=self._table)
        with self._pool.connection() as conn:
            rows = conn.execute(q, {"types": list(f.allowed_client_types), "status": f.status,
                                    "lang": f.language}).fetchall()
        docs: dict[str, dict] = {}
        for r in rows:
            d = docs.setdefault(r["title"], {"title": r["title"], "product": r["product"], "sections": []})
            if r["section_path"] and r["section_path"] not in d["sections"]:
                d["sections"].append(r["section_path"])
        result = list(docs.values())
        self._catalog_cache[key] = (time.monotonic(), result)
        return result
