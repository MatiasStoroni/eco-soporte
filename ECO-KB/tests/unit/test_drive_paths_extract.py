from pathlib import Path

import pytest

from eco_kb.drive.extract import UnsupportedFile, extract_markdown, is_supported
from eco_kb.drive.sync import source_path
from eco_kb.ingest.loader import load_text

VALID = {"hotel", "bodega"}


def test_source_path_aliases():
    assert source_path(("Soporte", "Hotel"), "Manual.md") == Path("support/hotel/Manual.md")
    assert source_path(("ventas", "Común"), "A") == Path("sales/_common/A")
    assert source_path(("soporte",), "x.md") is None
    assert source_path(("a", "b", "c"), "x.md") is None


def test_is_supported():
    assert is_supported("a.pdf", "application/pdf")
    assert is_supported("Doc", "application/vnd.google-apps.document")
    assert not is_supported("a.xlsx", "application/vnd.ms-excel")


def test_extract_markdown_and_empty():
    assert extract_markdown("a.md", "text/markdown", "﻿# Hola".encode()) == "# Hola"
    with pytest.raises(UnsupportedFile):
        extract_markdown("a.md", "text/markdown", b"   ")


def test_load_text_without_frontmatter_uses_filename():
    recs = load_text(Path("support/hotel/Limpieza de baños.md"), "# Pasos\nUse EC-100 al 2%.", VALID)
    assert recs[0].title == "Limpieza de baños" and recs[0].doc_type == "documento"
    assert recs[0].client_types == ["hotel"]
