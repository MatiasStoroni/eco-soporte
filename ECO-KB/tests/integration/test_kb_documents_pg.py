"""Manifiesto kb_documents + hash de contenido + RLS, contra Postgres real. Vacía kb_chunks y kb_documents."""
from pathlib import Path

import pytest

from eco_kb.config import load_config
from eco_kb.db import make_pool
from eco_kb.ingest.loader import ChunkRecord, load_kb
from eco_kb.ingest.run import ingest
from eco_kb.kb_documents import DocumentStore, setup
from eco_kb.retrieval.filters import build_filter
from eco_kb.retrieval.store import PgChunkStore
from eco_kb.settings import get_settings
from tests.integration.test_isolation_pg import HashEmbedder

pytestmark = pytest.mark.integration

OZONO = "# Uso\nEl equipo de ozono se enciende 15 min con la habitación vacía. Ñandú, acción y señal."
X4 = "# Dilución\nX4 desincrustante: 20 ml/L para sarro en baños."


class CountingEmbedder(HashEmbedder):
    def __init__(self):
        self.calls = self.texts = 0

    def embed_documents(self, texts):
        self.calls += 1
        self.texts += len(texts)
        return super().embed_documents(texts)


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def total(stats, key):
    return sum(stats[key].values())


@pytest.fixture
def env(tmp_path):
    s = get_settings()
    cfg = load_config(s.clients_path, s.safety_path)
    admin = make_pool(s.database_url_admin, "t_docs", 3)
    setup(admin)
    with admin.connection() as c:
        c.execute("TRUNCATE kb_chunks, kb_documents")
    support = PgChunkStore("support", make_pool(s.database_url_support_ro, "t_docs_sup", 2))
    docs = DocumentStore(admin, list(cfg.clients))

    def load():
        records, errors = load_kb(tmp_path, set(cfg.clients))
        assert not errors
        return records

    yield tmp_path, admin, support, docs, load
    with admin.connection() as c:
        c.execute("TRUNCATE kb_chunks, kb_documents")
    support._pool.close()
    admin.close()


def visible_titles(store, emb, client_type, q="ozono"):
    f = build_filter("support", client_type, "es")
    return {c["source_id"] for c in store.search(emb.embed_query(q), q, f)}


def test_setup_is_idempotent(env):
    _, admin, *_ = env
    assert setup(admin) == [] and setup(admin) == []


def test_python_hash_matches_sql(env):
    _, admin, *_ = env
    content = "Ficha > Dilución\n\nAcción del ñandú: 20 ml/L — señal ¿ok?"
    with admin.connection() as c:
        sql_hash = c.execute("SELECT encode(sha256(convert_to(%s, 'UTF8')), 'hex') AS h", (content,)).fetchone()["h"]
    assert sql_hash == ChunkRecord.make_hash(content)


def test_folder_is_only_the_initial_suggestion(env):
    root, admin, support, docs, load = env
    write(root, "soporte/hotel/ozono.md", OZONO)
    write(root, "soporte/comun/x4.md", X4)
    write(root, "soporte/equipos/nuevo.md", "# Nuevo\nEquipo de ozono portátil.")
    emb = CountingEmbedder()
    ingest(load(), admin, emb)
    by_id = {d["source_id"]: d for d in docs.list()}
    assert by_id["soporte/hotel/ozono.md"]["client_types"] == ["hotel"]
    assert by_id["soporte/comun/x4.md"]["client_types"] == ["common"]
    assert by_id["soporte/equipos/nuevo.md"]["client_types"] == []  # sin habilitar
    assert by_id["soporte/hotel/ozono.md"]["chunks"] == 1
    assert "soporte/equipos/nuevo.md" not in visible_titles(support, emb, "hotel")


def test_set_visibility_moves_between_types_without_embedding(env):
    root, admin, support, docs, load = env
    write(root, "soporte/hotel/ozono.md", OZONO)
    emb = CountingEmbedder()
    ingest(load(), admin, emb)
    calls = emb.calls
    assert visible_titles(support, emb, "hotel") == {"soporte/hotel/ozono.md"}
    assert visible_titles(support, emb, "restaurante") == set()

    doc = docs.set_visibility("support", "soporte/hotel/ozono.md", ["hotel", "restaurante"], "Ana")
    assert doc["client_types"] == ["hotel", "restaurante"] and doc["updated_by"] == "Ana"
    assert visible_titles(support, emb, "restaurante") == {"soporte/hotel/ozono.md"}

    docs.set_visibility("support", "soporte/hotel/ozono.md", [], "Ana")  # sin habilitar
    assert visible_titles(support, emb, "hotel") == set() and visible_titles(support, emb, "restaurante") == set()
    assert emb.calls == calls  # cambiar permisos nunca embebe
    assert docs.set_visibility("support", "soporte/hotel/no-existe.md", ["hotel"], "Ana") is None


def test_reingest_keeps_panel_visibility(env):
    root, admin, support, docs, load = env
    write(root, "soporte/hotel/ozono.md", OZONO)
    emb = CountingEmbedder()
    ingest(load(), admin, emb)
    docs.set_visibility("support", "soporte/hotel/ozono.md", ["restaurante"], "Ana")

    stats = ingest(load(), admin, emb)
    assert total(stats, "unchanged") == 1 and total(stats, "embedded") == 0
    assert visible_titles(support, emb, "restaurante") == {"soporte/hotel/ozono.md"}
    assert visible_titles(support, emb, "hotel") == set()

    # Editar el contenido re-embebe ese fragmento, pero tampoco toca la visibilidad.
    write(root, "soporte/hotel/ozono.md", OZONO.replace("15 min", "20 min"))
    stats = ingest(load(), admin, emb)
    assert total(stats, "embedded") == 1
    assert {d["source_id"]: d["client_types"] for d in docs.list()} == {"soporte/hotel/ozono.md": ["restaurante"]}


def test_moved_or_duplicated_file_is_embedded_once(env):
    root, admin, support, docs, load = env
    write(root, "soporte/hotel/ozono.md", OZONO)
    emb = CountingEmbedder()
    assert total(ingest(load(), admin, emb), "embedded") == 1
    texts = emb.texts

    write(root, "soporte/restaurante/ozono.md", OZONO)  # duplicado
    stats = ingest(load(), admin, emb)
    assert total(stats, "reused") == 1 and total(stats, "embedded") == 0

    (root / "soporte/hotel/ozono.md").unlink()  # movido: queda solo la copia de restaurante
    stats = ingest(load(), admin, emb)
    assert total(stats, "deleted") == 1 and total(stats, "embedded") == 0
    assert emb.texts == texts
    assert [d["source_id"] for d in docs.list()] == ["soporte/restaurante/ozono.md"]  # el manifiesto se limpia

    # Mover a otra carpeta (otro source_id) reutiliza el embedding aunque el original se borre en la misma pasada.
    (root / "soporte/comun").mkdir()
    (root / "soporte/restaurante/ozono.md").rename(root / "soporte/comun/ozono.md")
    stats = ingest(load(), admin, emb)
    assert total(stats, "reused") == 1 and total(stats, "deleted") == 1 and emb.texts == texts


def test_metadata_change_updates_without_embedding(env):
    root, admin, support, docs, load = env
    write(root, "soporte/hotel/x4.md", "---\nproduct: X4\ndoc_type: ficha\n---\n" + X4)
    emb = CountingEmbedder()
    ingest(load(), admin, emb)
    write(root, "soporte/hotel/x4.md", "---\nproduct: X4 Desincrustante\ndoc_type: ficha técnica\n---\n" + X4)
    stats = ingest(load(), admin, emb)
    assert total(stats, "meta_only") == 1 and total(stats, "embedded") == 0
    with admin.connection() as c:
        row = c.execute("SELECT product, doc_type FROM kb_chunks").fetchone()
    assert row == {"product": "X4 Desincrustante", "doc_type": "ficha técnica"}


def test_migration_rerun_does_not_reembed(env):
    """Simula una base de antes del cambio (hash viejo con metadatos): la migración lo recalcula en SQL."""
    root, admin, support, docs, load = env
    write(root, "soporte/hotel/ozono.md", OZONO)
    write(root, "soporte/comun/x4.md", X4)
    emb = CountingEmbedder()
    ingest(load(), admin, emb)
    texts = emb.texts
    with admin.connection() as c:
        c.execute("UPDATE kb_chunks SET content_hash = 'hash-viejo'")
        c.execute("DELETE FROM kb_documents")
        c.execute("DELETE FROM kb_schema_migrations WHERE version = 1")
    assert setup(admin) == [1]
    assert {d["source_id"]: d["client_types"] for d in docs.list()} == {
        "soporte/hotel/ozono.md": ["hotel"], "soporte/comun/x4.md": ["common"]}  # sembrado desde kb_chunks
    stats = ingest(load(), admin, emb)
    assert total(stats, "unchanged") == 2 and emb.texts == texts
