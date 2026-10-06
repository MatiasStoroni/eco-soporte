import logging
from pathlib import Path

import pytest

from eco_kb.ingest.format_check import check_format
from eco_kb.ingest.loader import load_text, parse_markdown
from eco_kb.ingest.restructure import Borrador, Observacion, Seccion, main, process

VALID = {"hotel", "bodega", "restaurante", "generic"}
KB = Path(__file__).resolve().parents[2] / "docs" / "estructurados"

GOOD = """---
title: X4 Desincrustante - Ficha técnica
product: X4
---

## Qué es X4 Desincrustante
Producto ácido para sarro y cal.

## Dilución de X4 para baños
Usar 250 ml de X4 en 750 ml de agua ozonizada. Tiempo de contacto: 3-5 min.
"""
# Lo típico de un PDF pasado a texto: sin secciones y largo.
BAD = "X4 DESINCRUSTANTE FICHA TECNICA\n" + "Usar 250 ml de X4 en 750 ml de agua. Contacto 3-5 min. " * 40


class FakeAI:
    def __init__(self, extra="", drop=False, obs=()):
        self.calls, self.extra, self.drop, self.obs = [], extra, drop, list(obs)

    def restructure(self, src, audience):
        self.calls.append(("restructure", src.path.name, audience))
        dosis = "" if self.drop else "Usar 250 ml de X4 en 750 ml de agua. Contacto 3-5 min."
        return Borrador(titulo="X4 Desincrustante - Ficha técnica", producto="X4", tipo_documento="ficha_tecnica",
                        secciones=[Seccion(titulo="Dilución de X4 para baños", contenido=f"{dosis} {self.extra}"),
                                   Seccion(titulo="Tabla de X4", contenido="# no es un título\nfila", desde_imagen=True)],
                        pendientes=["La página 2 está borrosa."])

    def verify(self, src, draft):
        self.calls.append(("verify", src.path.name))
        return self.obs


def write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# --- check_format (sin IA) ----------------------------------------------------------------------------------

@pytest.mark.parametrize("doc", sorted(KB.rglob("*.md")), ids=lambda p: p.name)
def test_current_kb_documents_are_valid_as_is(doc):
    assert check_format(doc.read_text(encoding="utf-8")).ok  # no se gastaría IA en ninguno


def test_check_format_problems():
    assert check_format(GOOD).ok
    assert not check_format(BAD).ok  # largo y sin secciones
    huge = "---\ntitle: T\n---\n## Tema\n" + "palabra " * 600
    assert any("demasiado larga" in p for p in check_format(huge).problems)
    per_line = "---\ntitle: T\n---\n## Tema\n" + "\n".join(f"pal{i}" for i in range(40))
    assert any("líneas sueltas" in p for p in check_format(per_line).problems)
    assert any("rotos" in p for p in check_format(GOOD + "\ntexto (cid:12) roto").problems)
    assert any("frontmatter" in p for p in check_format("---\ntitle: T\nclient_types: [hotel]\n---\n## A\nb").problems)
    assert any("imágenes" in p for p in check_format(GOOD, empty_pages=[2]).problems)


def test_check_format_warnings_do_not_block():
    rep = check_format("## Información\nUsar 20ml o 20 ml.")
    assert rep.ok and len(rep.warnings) == 3  # sin título, título genérico, cifras de dos formas
    assert check_format("## Temperaturas de X5\nEntre 5°C y 25°C, 20 ml/L.").warnings == ["sin título (frontmatter `title`): se usará el nombre del archivo"]


# --- process: cuándo se usa IA ------------------------------------------------------------------------------

def test_valid_file_is_kept_without_ai_and_not_reprocessed(tmp_path):
    src = write(tmp_path / "in" / "x4.md", GOOD)
    out_dir = tmp_path / "kb" / "soporte" / "borradores"
    ai = FakeAI()
    r = process(src, "soporte", out_dir, ai, VALID)
    assert r.action == "kept" and not ai.calls
    raw, body = parse_markdown(r.output.read_text(encoding="utf-8"))
    assert raw["title"] == "X4 Desincrustante - Ficha técnica" and raw["fuente_sha256"]
    assert body.strip() == parse_markdown(GOOD)[1].strip()  # el contenido queda igual

    assert process(src, "soporte", out_dir, ai, VALID).action == "skipped"
    write(src, GOOD.replace("3-5 min", "4-6 min"))  # la fuente cambió y el destino no se editó: se actualiza
    assert process(src, "soporte", out_dir, ai, VALID).action == "kept"
    assert "4-6 min" in r.output.read_text(encoding="utf-8") and not ai.calls


def test_invalid_file_uses_ai_once_and_writes_review(tmp_path):
    src = write(tmp_path / "in" / "x4.md", BAD)
    out_dir = tmp_path / "kb" / "soporte" / "borradores"
    ai = FakeAI(extra="Para mármol, 30 ml.", obs=[Observacion(seccion="Tabla", texto="fila", problema="agregado")])
    r = process(src, "soporte", out_dir, ai, VALID)
    assert r.action == "ai" and [c[0] for c in ai.calls] == ["restructure", "verify"]
    md = r.output.read_text(encoding="utf-8")
    assert "## Dilución de X4 para baños" in md and "### no es un título" in md  # sin # sueltos que corten secciones
    recs = load_text(Path("soporte/borradores/x4.md"), md, VALID)
    assert recs[0].client_types == [] and {x.section_path for x in recs} == {"Dilución de X4 para baños", "Tabla de X4"}

    report = (out_dir / "x4.revision.txt").read_text(encoding="utf-8")
    assert "30ml" in report  # cifra que no estaba en el original
    assert "agregado" in report and "Tabla de X4" in report and "borrosa" in report
    assert r.alerts == 2

    assert process(src, "soporte", out_dir, ai, VALID).action == "skipped"  # no se vuelve a gastar IA
    assert len(ai.calls) == 2


def test_figures_from_images_are_listed_not_flagged(tmp_path):
    class ImageAI(FakeAI):
        def restructure(self, src, audience):
            b = super().restructure(src, audience)
            b.secciones[1].contenido = "Densidad (20 °C): 1,03 g/mL"  # solo estaba en una imagen del PDF
            return b

    src = write(tmp_path / "x4.md", BAD)
    r = process(src, "soporte", tmp_path / "out", ImageAI(), VALID)
    report = (tmp_path / "out" / "x4.revision.txt").read_text(encoding="utf-8")
    assert "1.03g" not in report and "Secciones que salen de imágenes" in report and r.alerts == 0


def test_lost_figures_are_reported(tmp_path):
    src = write(tmp_path / "x4.md", BAD)
    r = process(src, "soporte", tmp_path / "out", FakeAI(drop=True), VALID)
    report = (tmp_path / "out" / "x4.revision.txt").read_text(encoding="utf-8")
    assert "no aparecen en el borrador" in report and "250ml" in report and r.alerts >= 1


def test_hand_edited_draft_is_never_overwritten(tmp_path):
    src = write(tmp_path / "x4.md", BAD)
    out_dir = tmp_path / "out"
    ai = FakeAI()
    r = process(src, "soporte", out_dir, ai, VALID)
    write(r.output, r.output.read_text(encoding="utf-8") + "\nCorrección de la revisora.\n")
    write(src, BAD + " Nueva versión.")
    assert process(src, "soporte", out_dir, ai, VALID).action == "protected"
    assert "Corrección de la revisora" in r.output.read_text(encoding="utf-8") and len(ai.calls) == 2
    assert process(src, "soporte", out_dir, ai, VALID, force=True).action == "ai"


def test_existing_file_not_made_by_the_tool_is_protected(tmp_path):
    out_dir = tmp_path / "out"
    write(out_dir / "x4.md", GOOD)  # p. ej. un documento ya publicado con el mismo nombre
    assert process(write(tmp_path / "in" / "x4.md", BAD), "soporte", out_dir, FakeAI(), VALID).action == "protected"


def test_only_check_and_in_place(tmp_path):
    bad = write(tmp_path / "a.md", BAD)
    ai = FakeAI()
    r = process(bad, "soporte", tmp_path / "out", ai, VALID, only_check=True)
    assert r.action == "invalid" and not ai.calls and not (tmp_path / "out").exists()
    good = write(tmp_path / "kb" / "soporte" / "hotel" / "x4.md", GOOD)  # validar un documento en su lugar
    r = process(good, "soporte", good.parent, ai, VALID)
    assert r.action == "kept" and good.read_text(encoding="utf-8") == GOOD


def test_cli_solo_validar(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    write(tmp_path / "docs" / "bien.md", GOOD)
    write(tmp_path / "docs" / "mal.md", BAD)
    write(tmp_path / "docs" / "nota.txt", "x")
    code = main([str(tmp_path / "docs"), "--audiencia", "soporte", "--solo-validar",
                 "--destino", str(tmp_path / "kb")])
    assert code == 0 and not (tmp_path / "kb").exists()
    assert "TAL CUAL" in caplog.text and "NECESITA IA" in caplog.text and "nota.txt" not in caplog.text
