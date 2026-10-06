from pathlib import Path

import pytest

from eco_kb.ingest.chunker import chunk_document, split_by_size
from eco_kb.ingest.loader import IngestError, derive_from_path, load_file

VALID = {"hotel", "bodega"}


def test_header_and_sections():
    chunks = chunk_document("Manual", "# A\ntexto a\n## B\ntexto b")
    assert chunks[0] == ("A", "Manual > A\n\ntexto a")
    assert chunks[1][0] == "A > B"


def test_size_split_with_overlap():
    text = " ".join(f"palabra{i}" for i in range(2000))
    parts = split_by_size(text, size=1000, overlap=100)
    assert len(parts) > 1 and all(len(p) <= 1000 for p in parts)
    assert parts[0].split()[-1] in parts[1]


def test_derive_from_path():
    assert derive_from_path(Path("support/_common/x.md"), VALID) == ("support", ["common"])
    assert derive_from_path(Path("sales/hotel/x.md"), VALID) == ("sales", ["hotel"])
    assert derive_from_path(Path("soporte/comun/x.md"), VALID) == ("support", ["common"])
    # Una carpeta que no es un tipo de cliente arranca sin habilitar (la visibilidad se elige en el panel).
    assert derive_from_path(Path("sales/equipos/x.md"), VALID) == ("sales", [])
    with pytest.raises(IngestError):
        derive_from_path(Path("support/x.md"), VALID)
    with pytest.raises(IngestError):
        derive_from_path(Path("admin/hotel/x.md"), VALID)


def test_frontmatter_cannot_set_audience(tmp_path):
    d = tmp_path / "support" / "hotel"
    d.mkdir(parents=True)
    f = d / "x.md"
    f.write_text("---\ntitle: T\ndoc_type: m\nclient_types: [bodega]\n---\n# H\ntexto", encoding="utf-8")
    with pytest.raises(IngestError):
        load_file(f, tmp_path, VALID)


def test_load_ok(tmp_path):
    d = tmp_path / "support" / "_common"
    d.mkdir(parents=True)
    f = d / "x.md"
    f.write_text("---\ntitle: T\ndoc_type: m\n---\n# H\ntexto", encoding="utf-8")
    recs = load_file(f, tmp_path, VALID)
    assert recs[0].audience == "support" and recs[0].client_types == ["common"]
    assert recs[0].chunk_id == "support/_common/x.md#0"
