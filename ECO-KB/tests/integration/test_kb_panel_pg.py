"""La KB gestionada desde el panel, contra Postgres real: subir → procesar → borrador/publicado → editar → borrar.
Vacía kb_chunks y kb_documents."""
from pathlib import Path

import pytest

from eco_kb.config import load_config
from eco_kb.db import make_pool
from eco_kb.ingest.loader import load_kb
from eco_kb.ingest.run import ingest
from eco_kb.kb_documents import DocumentNotFound, DocumentStore, recover_interrupted, setup
from eco_kb.retrieval.filters import build_filter
from eco_kb.retrieval.store import PgChunkStore
from eco_kb.settings import get_settings
from tests.integration.test_kb_documents_pg import CountingEmbedder, write
from tests.unit.test_restructure import BAD, GOOD, FakeAI

pytestmark = pytest.mark.integration


@pytest.fixture
def env(tmp_path):
    s = get_settings()
    cfg = load_config(s.clients_path, s.safety_path)
    admin = make_pool(s.database_url_admin, "t_panel", 3)
    setup(admin)
    with admin.connection() as c:
        c.execute("TRUNCATE kb_chunks, kb_documents")
    emb, ai = CountingEmbedder(), FakeAI()
    docs = DocumentStore(admin, list(cfg.clients), embedder=emb, ai=ai)
    support = PgChunkStore("support", make_pool(s.database_url_support_ro, "t_panel_sup", 2))
    yield docs, emb, ai, support, tmp_path, set(cfg.clients)
    with admin.connection() as c:
        c.execute("TRUNCATE kb_chunks, kb_documents")
    support._pool.close()
    admin.close()


def seen_by(store, emb, client_type, q="X4 dilución baños"):
    return {c["source_id"] for c in store.search(emb.embed_query(q), q, build_filter("support", client_type, "es"))}


def chunks(docs, sid):
    with docs.pool.connection() as c:
        return c.execute("SELECT count(*) AS n FROM kb_chunks WHERE source_id = %s", (sid,)).fetchone()["n"]


def test_valid_upload_is_published_without_ai(env):
    docs, emb, ai, support, *_ = env
    doc = docs.upload("support", "X4 ficha.md", GOOD.encode(), ["hotel"], "Ana")
    sid = doc["source_id"]
    assert sid == "soporte/panel/X4 ficha.md" and doc["state"] == "processing" and doc["client_types"] == ["hotel"]
    docs.process("support", sid, "Ana")
    got = docs.get("support", sid)
    assert got["state"] == "ready" and got["origin"] == "panel" and not got["has_draft"] and got["chunks"] == 2
    assert got["title"] == "X4 Desincrustante - Ficha técnica" and not ai.calls  # sin IA
    assert got["markdown"] == GOOD  # se publica tal cual vino
    assert seen_by(support, emb, "hotel") == {sid} and seen_by(support, emb, "bodega") == set()
    assert docs.original("support", sid) == ("X4 ficha.md", GOOD.encode())


def test_ai_draft_is_not_used_until_published(env):
    docs, emb, ai, support, *_ = env
    sid = docs.upload("support", "x4.pdf.md", BAD.encode(), ["common"], "Ana")["source_id"]
    docs.process("support", sid, "Ana")
    got = docs.get("support", sid)
    assert got["has_draft"] and got["chunks"] == 0 and got["review"]["used_ai"] and len(ai.calls) == 2
    assert "## Dilución de X4 para baños" in got["draft_markdown"] and got["title"] == "X4 Desincrustante - Ficha técnica"
    assert "sha256" not in got["draft_markdown"]  # la procedencia vive en la base, no en el texto que se edita
    assert seen_by(support, emb, "hotel") == set()  # el bot no ve borradores

    pub = docs.publish("support", sid, "Beto")
    assert pub["chunks"] == 2 and not pub["has_draft"] and pub["updated_by"] == "Beto"
    assert seen_by(support, emb, "hotel") == {sid}


def test_edit_published_keeps_live_version_until_publish(env):
    docs, emb, _, support, *_ = env
    sid = docs.upload("support", "x4.md", GOOD.encode(), ["hotel"], "Ana")["source_id"]
    docs.process("support", sid, "Ana")
    texts = emb.texts
    edited = GOOD.replace("3-5 min", "4-6 min")
    check = docs.save_draft("support", sid, edited, "Ana")
    assert check["problems"] == []
    with docs.pool.connection() as c:
        live = c.execute("SELECT string_agg(content, ' ') AS t FROM kb_chunks WHERE source_id = %s", (sid,)).fetchone()["t"]
    assert "3-5 min" in live  # el bot sigue con la versión publicada
    out = docs.publish("support", sid, "Ana")
    assert out["stats"]["embedded"] == 1 and out["stats"]["unchanged"] == 1 and emb.texts == texts + 1
    with pytest.raises(ValueError):  # el contenido nunca define la visibilidad
        docs.save_draft("support", sid, "---\nclient_types: [hotel]\n---\n## A\nb", "Ana")


def test_replace_keeps_visibility_and_same_name_is_the_same_document(env):
    docs, emb, *_ = env
    sid = docs.upload("support", "x4.md", GOOD.encode(), ["hotel", "bodega"], "Ana")["source_id"]
    docs.process("support", sid, "Ana")
    again = docs.upload("support", "x4.md", GOOD.replace("3-5", "4-6").encode(), ["generic"], "Ana")
    assert again["source_id"] == sid and again["client_types"] == ["bodega", "hotel"]  # se conserva
    docs.process("support", sid, "Ana")
    assert docs.get("support", sid)["chunks"] == 2 and len(docs.list()) == 1


def test_files_ingest_never_touches_panel_documents(env):
    docs, emb, _, support, tmp_path, valid = env
    write(tmp_path, "soporte/hotel/x4.md", GOOD)
    write(tmp_path, "soporte/comun/otro.md", "## Otro tema\nTexto de otro documento.")
    records, _ = load_kb(tmp_path, valid)
    ingest(records, docs.pool, emb, texts={"soporte/hotel/x4.md": GOOD})
    sid = "soporte/hotel/x4.md"
    assert docs.get("support", sid)["origin"] == "files" and docs.get("support", sid)["markdown"] == GOOD

    # Se edita desde el panel: pasa a gestionarse ahí.
    docs.save_draft("support", sid, GOOD.replace("3-5 min", "9 min"), "Ana")
    docs.publish("support", sid, "Ana")
    panel_sid = docs.upload("support", "nuevo.md", GOOD.encode(), ["common"], "Ana")["source_id"]
    docs.process("support", panel_sid, "Ana")

    # La ingesta desde archivos (con el archivo viejo, y sin el nuevo) no pisa ni borra nada del panel.
    (tmp_path / "soporte/comun/otro.md").unlink()
    records, _ = load_kb(tmp_path, valid)
    stats = ingest(records, docs.pool, emb)
    assert sum(stats["panel"].values()) == 2 and sum(stats["deleted"].values()) == 1  # solo se borró "otro"
    assert "9 min" in docs.get("support", sid)["markdown"] and chunks(docs, panel_sid) == 2
    assert {d["source_id"] for d in docs.list()} == {sid, panel_sid}


def test_discard_and_delete(env):
    docs, emb, _, support, *_ = env
    sid = docs.upload("support", "x4.md", BAD.encode(), [], "Ana")["source_id"]
    docs.process("support", sid, "Ana")
    assert docs.discard_draft("support", sid) is None and docs.get("support", sid) is None  # nunca publicado

    sid = docs.upload("support", "x4.md", GOOD.encode(), ["hotel"], "Ana")["source_id"]
    docs.process("support", sid, "Ana")
    docs.save_draft("support", sid, GOOD + "\nAgregado.", "Ana")
    kept = docs.discard_draft("support", sid)
    assert kept and not kept["has_draft"] and kept["chunks"] == 2  # publicado: solo se descarta el borrador
    docs.delete("support", sid)
    assert docs.get("support", sid) is None and chunks(docs, sid) == 0 and seen_by(support, emb, "hotel") == set()
    with pytest.raises(DocumentNotFound):
        docs.delete("support", sid)


def test_errors_are_stored_not_raised(env):
    docs, emb, *_ = env
    docs.ai = None
    sid = docs.upload("support", "x4.md", BAD.encode(), [], "Ana")["source_id"]
    docs.process("support", sid, "Ana")  # necesita IA y no hay: no lanza
    got = docs.get("support", sid)
    assert got["state"] == "error" and "GOOGLE_API_KEY" in got["error"]
    with pytest.raises(ValueError):
        docs.upload("support", "x4.exe", b"MZ", [], "Ana")
    with pytest.raises(ValueError):
        docs.upload("support", "x4.md", GOOD.encode(), ["banco"], "Ana")
    with pytest.raises(DocumentNotFound):
        docs.upload("support", "x4.md", GOOD.encode(), [], "Ana", source_id="soporte/hotel/no.md")

    docs.upload("support", "y.md", GOOD.encode(), [], "Ana")  # queda "procesando" y la API se reinicia
    assert recover_interrupted(docs.pool) == 1
    assert docs.get("support", "soporte/panel/y.md")["state"] == "error"
