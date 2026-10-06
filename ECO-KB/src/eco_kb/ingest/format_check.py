"""¿Un documento ya está listo para el RAG? Chequeo determinístico, sin IA.

Si no hay `problems`, el documento se usa tal cual (no se gasta IA en reformatearlo). Los `warnings` se informan
pero no obligan a reprocesar. Las reglas siguen lo que necesitan el chunker y la recuperación:

- un título (frontmatter `title` o un `# Título` al principio; si no, se usa el nombre del archivo);
- una sección `##` por tema: un documento largo sin secciones queda en fragmentos cortados a ciegas;
- secciones que entren en un fragmento (`CHUNK_CHARS`): si no, el tema se parte en dos;
- texto limpio: sin restos de extracción de PDF (una palabra por línea, caracteres rotos) ni páginas sin texto.
"""
import re
from dataclasses import dataclass, field

from eco_kb.ingest.chunker import CHUNK_CHARS, split_sections
from eco_kb.ingest.loader import FORBIDDEN_FRONTMATTER, IngestError, parse_markdown

LONG_DOC_CHARS = 1500       # a partir de acá, sin secciones es un problema
MIN_SECTIONS_LONG_DOC = 2
_GENERIC_HEADINGS = {"introducción", "introduccion", "información", "informacion", "general", "otros", "varios",
                     "datos", "notas", "contenido", "índice", "indice", "anexo", "detalles"}
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+")
_BROKEN_CHARS = re.compile(r"�|\(cid:\d+\)")
# Misma cifra escrita de dos formas ("20ml/L" y "20 ml/L"): el RAG las compara literalmente.
_UNIT = r"(ml|l|g|kg|mg|ppm|min|°c|%)(?![a-záéíóúñ])"
_GLUED_UNIT = re.compile(rf"\d{_UNIT}", re.IGNORECASE)
_SPACED_UNIT = re.compile(rf"\d +{_UNIT}", re.IGNORECASE)


@dataclass
class FormatReport:
    problems: list[str] = field(default_factory=list)  # obligan a reformatear (con IA)
    warnings: list[str] = field(default_factory=list)  # se informan; el documento se usa igual
    title: str = ""
    sections: int = 0

    @property
    def ok(self) -> bool:
        return not self.problems


def _prose_lines(body: str) -> list[str]:
    """Líneas de texto corrido: sin títulos, ítems de lista, tablas ni vacías."""
    return [ln.strip() for ln in body.splitlines()
            if ln.strip() and not _HEADING.match(ln) and not _LIST_ITEM.match(ln) and not ln.lstrip().startswith("|")]


def check_format(text: str, empty_pages: list[int] | None = None) -> FormatReport:
    """`text` = markdown (con o sin frontmatter). `empty_pages` = páginas de un PDF sin texto extraíble."""
    rep = FormatReport()
    try:
        raw, body = parse_markdown(text)
    except IngestError as exc:
        rep.problems.append(f"frontmatter inválido: {exc}")
        return rep
    bad = FORBIDDEN_FRONTMATTER & set(raw)
    if bad:
        rep.problems.append(f"el frontmatter define {sorted(bad)}: la visibilidad se elige en el panel, no en el archivo")

    first_h1 = next((m[2] for ln in body.splitlines() if (m := _HEADING.match(ln)) and m[1] == "#"), "")
    rep.title = str(raw.get("title") or first_h1 or "")
    if not rep.title:
        rep.warnings.append("sin título (frontmatter `title`): se usará el nombre del archivo")

    if empty_pages:
        rep.problems.append(f"páginas sin texto extraíble (probablemente imágenes): {empty_pages}")

    sections = split_sections(body)
    headed = [(path, txt) for path, txt in sections if path]
    rep.sections = len(headed)
    size = len(body.strip())
    if not size:
        rep.problems.append("sin contenido")
        return rep
    if size > LONG_DOC_CHARS and len(headed) < MIN_SECTIONS_LONG_DOC:
        rep.problems.append(f"{size} caracteres sin secciones `##`: hay que dividirlo por tema")
    for path, txt in sections:
        if len(txt) > CHUNK_CHARS:
            rep.problems.append(f"sección «{path or '(sin título)'}» demasiado larga ({len(txt)} caracteres; "
                                f"máximo {CHUNK_CHARS}): dividirla por tema")

    lines = _prose_lines(body)
    if len(lines) >= 20:
        short = sum(1 for ln in lines if len(ln.split()) <= 2)
        if short / len(lines) > 0.4:
            rep.problems.append(f"texto cortado en líneas sueltas ({short} de {len(lines)} líneas con 1-2 palabras): "
                                "típico de una extracción de PDF")
    broken = len(_BROKEN_CHARS.findall(body))
    if broken:
        rep.problems.append(f"{broken} caracteres rotos de la extracción (� o (cid:N))")

    for line in body.splitlines():
        m = _HEADING.match(line)
        if m and len(m[1]) <= 2:
            h = m[2].strip().lower().rstrip(":")
            if h in _GENERIC_HEADINGS or len(h) < 4 or h.replace(".", "").isdigit():
                rep.warnings.append(f"título de sección poco descriptivo: «{m[2]}» (mejor con el tema y el producto)")
    mixed = ({u.lower() for u in _GLUED_UNIT.findall(body)} & {u.lower() for u in _SPACED_UNIT.findall(body)})
    if mixed:
        rep.warnings.append(f"cifras escritas de dos formas (pegadas y separadas) en: {', '.join(sorted(mixed))} "
                            "(p. ej. «20ml» y «20 ml»): conviene unificarlas")
    return rep
