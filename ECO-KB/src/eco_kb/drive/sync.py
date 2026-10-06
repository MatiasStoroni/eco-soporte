"""Sincroniza la carpeta de Drive con la KB.

    uv run python -m eco_kb.drive.sync --once
    uv run python -m eco_kb.drive.sync --loop [--interval-minutes 15]

Cada pasada lista el árbol completo (barato) y solo descarga/embebe lo nuevo o modificado.
Estructura esperada en Drive: <raíz>/<soporte|ventas>/<carpeta>/<archivo>. La audiencia sale de la carpeta de
primer nivel. La de segundo nivel es solo la visibilidad SUGERIDA para un archivo nuevo (`hotel` → Hotel,
`comun` → todos, cualquier otra → sin habilitar); después manda lo que se elija en el panel (/admin → Archivos).
"""
import argparse
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from eco_kb.config import load_config
from eco_kb.db import make_pool
from eco_kb.drive.client import DriveClient, GoogleDriveClient, load_credentials
from eco_kb.drive.extract import extract_markdown, is_supported
from eco_kb.ingest.loader import IngestError, load_text, normalize_part
from eco_kb.ingest.run import ingest
from eco_kb.llm import Embedder, GeminiEmbedder
from eco_kb.settings import get_settings

log = logging.getLogger("eco_kb.drive.sync")
LOCK_ID = 727_001

_DDL = """
CREATE TABLE IF NOT EXISTS kb_sources (
    source_id text PRIMARY KEY, drive_file_id text NOT NULL, modified_time text NOT NULL,
    md5 text NOT NULL DEFAULT '', updated_at timestamptz NOT NULL DEFAULT now()
)"""


class SyncAborted(Exception):
    pass


class SyncBusy(Exception):
    pass


@dataclass
class SyncReport:
    processed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)   # formato no soportado / ruta inválida
    failed: list[str] = field(default_factory=list)    # error al leer: se conserva la versión anterior
    stats: dict = field(default_factory=dict)


def source_path(parts: tuple[str, ...], name: str) -> Path | None:
    norm = [normalize_part(p) for p in parts]
    if len(norm) != 2:
        return None
    return Path(*norm, name.replace("/", "-").strip())


def sync_once(client: DriveClient, root_id: str, pool, embedder: Embedder,
              valid_client_types: set[str], force: bool = False) -> SyncReport:
    with pool.connection() as lock:
        if not lock.execute("SELECT pg_try_advisory_lock(%s) AS ok", (LOCK_ID,)).fetchone()["ok"]:
            raise SyncBusy("otra sincronización en curso")
        try:
            return _sync(client, root_id, pool, embedder, valid_client_types, force)
        finally:
            lock.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))


def _sync(client, root_id, pool, embedder, valid_client_types, force) -> SyncReport:
    rep = SyncReport()
    with pool.connection() as conn:
        conn.execute(_DDL)
        known = {r["source_id"]: r for r in conn.execute("SELECT * FROM kb_sources").fetchall()}

    files = client.list_files(root_id)
    keep: set[str] = set()
    records, touched = [], {}
    for f in files:
        path = source_path(f.parts, f.name)
        if path is None:
            rep.skipped.append(f"{'/'.join((*f.parts, f.name))}: ruta inválida (esperado soporte|ventas/<carpeta>/archivo)")
            continue
        if not is_supported(f.name, f.mime):
            rep.skipped.append(f"{path}: formato no soportado ({f.mime})")
            continue
        sid = path.as_posix()
        if sid in keep:
            rep.skipped.append(f"{sid}: nombre duplicado en la misma carpeta")
            continue
        keep.add(sid)  # presente en Drive: protegido de borrado aunque falle su lectura
        prev = known.get(sid)
        if prev and prev["modified_time"] == f.modified and prev["md5"] == f.md5:
            rep.unchanged.append(sid)
            continue
        try:
            text = extract_markdown(f.name, f.mime, client.download(f))
            recs = load_text(path, text, valid_client_types)
            if not recs:
                raise IngestError("sin contenido")
        except Exception as exc:  # un archivo malo no debe tumbar la sincronización
            rep.failed.append(f"{sid}: {exc}")
            continue
        records += recs
        touched[sid] = f
        rep.processed.append(sid)

    removed = set(known) - keep
    if not force and removed and len(known) >= 4 and len(removed) > len(known) / 2:
        raise SyncAborted(
            f"se borrarían {len(removed)} de {len(known)} fuentes (¿permisos/carpeta equivocada?). "
            "Revisa Drive o usa --force."
        )
    rep.removed = sorted(removed)

    rep.stats = ingest(records, pool, embedder, keep_sources=keep)
    with pool.connection() as conn, conn.transaction():
        for sid, f in touched.items():
            conn.execute(
                "INSERT INTO kb_sources (source_id, drive_file_id, modified_time, md5) VALUES (%s,%s,%s,%s) "
                "ON CONFLICT (source_id) DO UPDATE SET drive_file_id = EXCLUDED.drive_file_id, "
                "modified_time = EXCLUDED.modified_time, md5 = EXCLUDED.md5, updated_at = now()",
                (sid, f.id, f.modified, f.md5),
            )
        for sid in removed:
            conn.execute("DELETE FROM kb_sources WHERE source_id = %s", (sid,))
    return rep


def _log_report(rep: SyncReport) -> None:
    log.info("sync: %d procesados, %d sin cambios, %d borrados, %d omitidos, %d con error",
             len(rep.processed), len(rep.unchanged), len(rep.removed), len(rep.skipped), len(rep.failed))
    for kind, items in (("PROCESADO", rep.processed), ("BORRADO", rep.removed),
                        ("OMITIDO", rep.skipped), ("ERROR", rep.failed)):
        for i in items:
            (log.warning if kind in ("OMITIDO", "ERROR") else log.info)("%s %s", kind, i)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--loop", action="store_true")
    p.add_argument("--once", action="store_true")
    p.add_argument("--force", action="store_true", help="permite borrados masivos")
    p.add_argument("--interval-minutes", type=int)
    args = p.parse_args()

    s = get_settings()
    if not s.drive_root_folder_id:
        log.error("Falta DRIVE_ROOT_FOLDER_ID")
        return 2
    cfg = load_config(s.clients_path, s.safety_path)
    pool = make_pool(s.database_url_admin, "drive_sync", max_size=3)
    client = GoogleDriveClient(load_credentials(s.google_oauth_token_path))
    embedder = GeminiEmbedder(s.google_api_key, s.embedding_model, s.embedding_dim)
    interval = (args.interval_minutes or s.drive_sync_interval_minutes) * 60
    code = 0
    while True:
        try:
            rep = sync_once(client, s.drive_root_folder_id, pool, embedder, set(cfg.clients), args.force)
            _log_report(rep)
            code = 1 if rep.failed else 0
        except SyncBusy as exc:
            log.info("%s", exc)
        except Exception:
            log.exception("sync falló")
            code = 1
        if not args.loop:
            break
        time.sleep(interval)
    pool.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
