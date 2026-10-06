from pathlib import Path

import pytest

from eco_kb.ingest.loader import ChunkRecord, load_text
from eco_kb.kb_documents import normalize_client_types

VALID = {"hotel", "bodega"}
DOC = "---\ntitle: Ficha X4\nproduct: X4\n---\n# Dilución\nUsar 20 ml/L en baños y cañerías."


def test_hash_depends_only_on_content():
    a = load_text(Path("soporte/hotel/x4.md"), DOC, VALID)[0]
    moved = load_text(Path("soporte/bodega/x4 copia.md"), DOC, VALID)[0]
    assert a.client_types == ["hotel"] and moved.client_types == ["bodega"]
    assert a.content_hash == moved.content_hash  # mover/duplicar/cambiar permisos no re-embebe

    edited = load_text(Path("soporte/hotel/x4.md"), DOC.replace("20 ml/L", "25 ml/L"), VALID)[0]
    assert edited.content_hash != a.content_hash
    retitled = load_text(Path("soporte/hotel/x4.md"), DOC.replace("Ficha X4", "Ficha técnica X4"), VALID)[0]
    assert retitled.content_hash != a.content_hash  # el título va en el encabezado del fragmento


def test_hash_is_plain_sha256_of_utf8():
    # Valor calculado con encode(sha256(convert_to(content, 'UTF8')), 'hex') en Postgres (lo que usa la
    # migración para recalcular los hashes sin re-embeber). test_kb_documents_pg lo verifica contra la base.
    assert ChunkRecord.make_hash("Ficha > Dilución\n\nñandú 20 ml/L") == (
        "0196734b50935af975585a602ecc8be3a3586dd8ac2437fff7b115c1246ec8dc")


def test_unknown_folder_loads_hidden():
    recs = load_text(Path("soporte/equipos/x4.md"), DOC, VALID)
    assert recs and all(r.client_types == [] for r in recs)


def test_normalize_client_types():
    valid = ["bodega", "hotel", "restaurante", "generic"]
    assert normalize_client_types(["hotel", "bodega", "hotel"], valid) == ["bodega", "hotel"]
    assert normalize_client_types(["hotel", "common"], valid) == ["common"]
    assert normalize_client_types([], valid) == []
    with pytest.raises(ValueError):
        normalize_client_types(["banco"], valid)
