import re

# Aproximación: 1 token ≈ 4 caracteres.
CHUNK_CHARS = 800 * 4
OVERLAP_CHARS = 100 * 4

_HEADING = re.compile(r"^(#{1,2})\s+(.+?)\s*$")


def split_sections(body: str) -> list[tuple[str, str]]:
    """Divide por headings # / ##. Devuelve (section_path, texto)."""
    h1, h2 = "", ""
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in body.splitlines():
        m = _HEADING.match(line)
        if m:
            if m.group(1) == "#":
                h1, h2 = m.group(2), ""
            else:
                h2 = m.group(2)
            sections.append((" > ".join(p for p in (h1, h2) if p), []))
        else:
            sections[-1][1].append(line)
    return [(path, "\n".join(lines).strip()) for path, lines in sections if "\n".join(lines).strip()]


def split_by_size(text: str, size: int = CHUNK_CHARS, overlap: int = OVERLAP_CHARS) -> list[str]:
    if len(text) <= size:
        return [text]
    parts: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):  # cortar en salto de párrafo/espacio si es posible
            cut = max(text.rfind("\n\n", start, end), text.rfind(" ", start + size // 2, end))
            if cut > start:
                end = cut
        parts.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [p for p in parts if p]


def chunk_document(title: str, body: str) -> list[tuple[str, str]]:
    """Devuelve [(section_path, texto_a_embeber)] con encabezado `Título > Sección`."""
    out: list[tuple[str, str]] = []
    for path, text in split_sections(body):
        header = f"{title} > {path}" if path else title
        for piece in split_by_size(text):
            out.append((path, f"{header}\n\n{piece}"))
    return out
