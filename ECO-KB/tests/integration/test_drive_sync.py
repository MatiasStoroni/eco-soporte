"""Sync Drive -> KB con un Drive falso, contra Postgres real. Vacía kb_chunks y kb_sources."""
import pytest

from eco_kb.config import load_config
from eco_kb.db import make_pool
from eco_kb.drive.client import DriveFile
from eco_kb.drive.sync import SyncAborted, sync_once
from eco_kb.settings import get_settings
from tests.integration.test_isolation_pg import HashEmbedder

pytestmark = pytest.mark.integration
MD = "text/markdown"


class FakeDrive:
    def __init__(self):
        self.files: dict[str, tuple[DriveFile, bytes]] = {}
        self.downloads = 0
        self.broken: set[str] = set()

    def put(self, fid, parts, name, text, modified="1"):
        self.files[fid] = (DriveFile(fid, name, MD, modified, "", tuple(parts)), text.encode())

    def list_files(self, root_id):
        return [f for f, _ in self.files.values()]

    def download(self, f):
        self.downloads += 1
        if f.id in self.broken:
            raise RuntimeError("boom")
        return self.files[f.id][1]


@pytest.fixture
def env():
    s = get_settings()
    cfg = load_config(s.clients_path, s.safety_path)
    pool = make_pool(s.database_url_admin, "t_drive", 3)
    with pool.connection() as c:
        c.execute("DROP TABLE IF EXISTS kb_sources")
        c.execute("TRUNCATE kb_chunks")
    yield pool, set(cfg.clients), HashEmbedder()
    with pool.connection() as c:
        c.execute("TRUNCATE kb_chunks")
        c.execute("DROP TABLE IF EXISTS kb_sources")
    pool.close()


def count(pool, where="true"):
    with pool.connection() as c:
        return c.execute(f"SELECT count(*) AS n FROM kb_chunks WHERE {where}").fetchone()["n"]


def test_upload_update_delete_cycle(env):
    pool, types, emb = env
    d = FakeDrive()
    d.put("1", ["Soporte", "Hotel"], "dilucion.md", "# Dilución\nEC-100 al 2%.")
    d.put("2", ["Ventas", "Común"], "gama.md", "# Gama\nEcoClean ofrece detergentes.")

    rep = sync_once(d, "root", pool, emb, types)
    assert sorted(rep.processed) == ["sales/_common/gama.md", "support/hotel/dilucion.md"]
    assert count(pool, "audience='support' AND client_types = ARRAY['hotel']") == 1
    assert count(pool, "audience='sales' AND client_types = ARRAY['common']") == 1

    # sin cambios: no se vuelve a descargar
    before = d.downloads
    rep = sync_once(d, "root", pool, emb, types)
    assert d.downloads == before and len(rep.unchanged) == 2

    # modificación: se re-procesa solo ese archivo
    d.put("1", ["Soporte", "Hotel"], "dilucion.md", "# Dilución\nEC-100 al 3%.", modified="2")
    rep = sync_once(d, "root", pool, emb, types)
    assert rep.processed == ["support/hotel/dilucion.md"]
    with pool.connection() as c:
        assert "3%" in c.execute("SELECT content FROM kb_chunks_support").fetchone()["content"]

    # borrado en Drive: se borra de la KB
    del d.files["2"]
    rep = sync_once(d, "root", pool, emb, types, force=True)
    assert rep.removed == ["sales/_common/gama.md"] and count(pool, "audience='sales'") == 0


def test_bad_paths_and_formats_are_skipped_not_ingested(env):
    pool, types, emb = env
    d = FakeDrive()
    d.put("1", [], "suelto.md", "# x\ny")                       # en la raíz
    d.put("2", ["Soporte", "Banco"], "a.md", "# x\ny")         # client_type desconocido
    d.put("3", ["Soporte", "Hotel"], "ok.md", "# x\ny")
    rep = sync_once(d, "root", pool, emb, types)
    assert rep.processed == ["support/hotel/ok.md"]
    assert len(rep.skipped) == 1 and len(rep.failed) == 1
    assert count(pool) == 1


def test_failed_download_keeps_previous_version(env):
    pool, types, emb = env
    d = FakeDrive()
    d.put("1", ["Soporte", "Hotel"], "a.md", "# A\nversión vieja")
    sync_once(d, "root", pool, emb, types)
    d.put("1", ["Soporte", "Hotel"], "a.md", "# A\nversión nueva", modified="2")
    d.broken.add("1")
    rep = sync_once(d, "root", pool, emb, types)
    assert rep.failed and count(pool) == 1
    with pool.connection() as c:
        assert "vieja" in c.execute("SELECT content FROM kb_chunks").fetchone()["content"]


def test_mass_deletion_guard(env):
    pool, types, emb = env
    d = FakeDrive()
    for i in range(5):
        d.put(str(i), ["Soporte", "Hotel"], f"f{i}.md", f"# T{i}\ntexto {i}")
    sync_once(d, "root", pool, emb, types)
    d.files.clear()  # p. ej. permisos revocados: Drive devuelve 0 archivos
    with pytest.raises(SyncAborted):
        sync_once(d, "root", pool, emb, types)
    assert count(pool) == 5
