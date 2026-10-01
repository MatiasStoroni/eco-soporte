"""Requiere `docker compose up -d`. Usa embeddings deterministas (sin red)."""
import hashlib
import math
from pathlib import Path

import psycopg
import pytest

from eco_kb.config import load_config
from eco_kb.db import make_pool
from eco_kb.ingest.loader import load_kb
from eco_kb.ingest.run import ingest
from eco_kb.retrieval.filters import build_filter
from eco_kb.retrieval.store import PgChunkStore
from eco_kb.settings import get_settings

pytestmark = pytest.mark.integration


class HashEmbedder:
    """Bolsa de palabras hasheada a 768 dims, normalizada: determinista y con algo de semántica léxica."""

    def _vec(self, text):
        v = [0.0] * 768
        for w in text.lower().split():
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 768] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    def embed_documents(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


@pytest.fixture(scope="module")
def env():
    s = get_settings()
    cfg = load_config(s.clients_path, s.safety_path)
    admin = make_pool(s.database_url_admin, "t_admin", 2)
    records, errors = load_kb(Path(s.kb_dir), set(cfg.clients))
    assert not errors
    emb = HashEmbedder()
    # OJO: estos tests usan embeddings falsos y vacían kb_chunks; tras ejecutarlos, relanza la ingesta real.
    admin.connection().__enter__().execute("TRUNCATE kb_chunks")
    ingest(records, admin, emb)
    stores = {
        "support": PgChunkStore("support", make_pool(s.database_url_support_ro, "t_sup", 2)),
        "sales": PgChunkStore("sales", make_pool(s.database_url_sales_ro, "t_sal", 2)),
    }
    yield s, emb, stores
    with admin.connection() as conn:
        conn.execute("TRUNCATE kb_chunks")
    admin.close()


QUERIES = ["dilución EC-100", "código de error E04", "depósitos acero inoxidable", "lavandería sábanas",
           "propuesta comercial", "barricas desinfección", "sostenibilidad biodegradable", "xyzzy"]


@pytest.mark.parametrize("flow", ["support", "sales"])
@pytest.mark.parametrize("ct", ["bodega", "hotel", "restaurante", "generic"])
def test_no_chunk_violates_filter(env, flow, ct):
    _, emb, stores = env
    f = build_filter(flow, ct, "es")
    seen = 0
    for q in QUERIES:
        for c in stores[flow].search(emb.embed_query(q), q, f):
            seen += 1
            assert c["audience"] == flow
            assert set(c["client_types"]) & {ct, "common"}
            assert set(c["client_types"]) <= {ct, "common"}
    assert seen > 0


def test_store_rejects_foreign_filter(env):
    _, emb, stores = env
    with pytest.raises(PermissionError):
        stores["sales"].search(emb.embed_query("x"), "x", build_filter("support", "hotel", "es"))


def test_sales_role_cannot_read_support_partition(env):
    s, _, _ = env
    with psycopg.connect(s.database_url_sales_ro) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT 1 FROM kb_chunks_support LIMIT 1")
    with psycopg.connect(s.database_url_sales_ro) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT 1 FROM kb_chunks LIMIT 1")


def test_support_role_cannot_read_sales_partition_nor_write(env):
    s, _, _ = env
    with psycopg.connect(s.database_url_support_ro) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT 1 FROM kb_chunks_sales LIMIT 1")
    with psycopg.connect(s.database_url_support_ro) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM kb_chunks_support")


def test_ingest_idempotent(env):
    s, emb, _ = env
    cfg = load_config(s.clients_path, s.safety_path)
    records, _ = load_kb(Path(s.kb_dir), set(cfg.clients))
    pool = make_pool(s.database_url_admin, "t_admin2", 2)
    stats = ingest(records, pool, emb)
    pool.close()
    assert sum(stats["inserted"].values()) == 0 and sum(stats["updated"].values()) == 0


def test_checkpointer_two_turns_same_thread(env, make_services):
    from langgraph.checkpoint.postgres import PostgresSaver

    from eco_kb.graph.builder import build_graph
    from tests.conftest import FakeLLM
    from tests.e2e.test_graph_fakes import SUP, script

    s, _, _ = env
    pool = make_pool(s.database_url_admin, "t_cp", 2)
    saver = PostgresSaver(pool)
    saver.setup()
    import uuid

    tid = f"cp-{uuid.uuid4().hex}"
    llm = FakeLLM(script(SUP["chunk_id"], "Diluya EC-100 al 2%."))
    graph = build_graph(make_services(llm, support_chunks=[SUP]), saver)
    cfg = {"configurable": {"thread_id": tid}, "recursion_limit": 25}
    body = {"session_id": tid, "is_registered": True, "client_type": "hotel", "language": "es",
            "message": "¿Cómo diluyo EC-100?"}
    graph.invoke(body, cfg)
    out = graph.invoke(body, cfg)
    assert out["final_answer"] == "Diluya EC-100 al 2%."
    assert len(out["messages"]) == 4
    pool.close()


def test_catalog_respects_filter(env):
    _, _, stores = env
    cat = stores["sales"].catalog(build_filter("sales", "hotel", "es"))
    assert cat and all(d["sections"] for d in cat)
    with pytest.raises(PermissionError):
        stores["sales"].catalog(build_filter("support", "hotel", "es"))
