"""Limpieza de texto extraído de PDF: ruido que empeora el troceado y los embeddings."""
import re
import unicodedata
from collections import Counter
from statistics import median

_DOT_LEADER = re.compile(r"(\.\s*){6,}")
_TOC_TITLES = {"contenido", "índice", "indice", "tabla de contenido"}
_PAGE_NUMBER = re.compile(r"^\d{1,3}$")
MIN_PAGE_CHARS = 30


def _is_word_per_line(lines: list[str]) -> bool:
    """PDFs maquetados donde cada palabra sale en su propia línea."""
    return len(lines) > 30 and median(len(line.split()) for line in lines) <= 1


def _join_tokens(lines: list[str]) -> str:
    text = " ".join(lines)
    text = re.sub(r"\s+([.,;:!?)\]%])", r"\1", text)
    text = re.sub(r"([(\[])\s+", r"\1", text)
    return re.sub(r"[ \t]{2,}", " ", text)


def clean_pdf_pages(pages: list[str]) -> tuple[str, list[int]]:
    """Devuelve (texto limpio, números de página (base 1) sin texto útil: probablemente imágenes)."""
    # NFKC: las ligaduras (ﬁ, ﬂ) rompen la búsqueda por texto ("superﬁcies" != "superficies")
    pages = [unicodedata.normalize("NFKC", p) for p in pages]
    split = [[ln.strip() for ln in p.splitlines() if ln.strip()] for p in pages]

    # cabeceras/pies repetidos en la mayoría de páginas
    repeated: set[str] = set()
    if len(pages) >= 3:
        counts = Counter(ln for lines in split for ln in set(lines) if len(ln) < 80)
        repeated = {ln for ln, n in counts.items() if n >= 0.6 * len(pages) and len(ln) >= 4}

    cleaned: list[str] = []
    empty_pages: list[int] = []
    for i, lines in enumerate(split, start=1):
        has_toc = sum(1 for ln in lines if _DOT_LEADER.search(ln)) >= 3
        kept = [
            ln for ln in lines
            if ln not in repeated
            and not _DOT_LEADER.search(ln)
            and not (has_toc and ln.lower() in _TOC_TITLES)
        ]
        if kept and _PAGE_NUMBER.match(kept[-1]):  # número de página suelto al pie
            kept = kept[:-1]
        text = _join_tokens(kept) if _is_word_per_line(kept) else "\n".join(kept)
        if len(text) < MIN_PAGE_CHARS:
            empty_pages.append(i)
        if text:
            cleaned.append(text)
    return "\n\n".join(cleaned), empty_pages
