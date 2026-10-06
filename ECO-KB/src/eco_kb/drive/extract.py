"""Convierte el contenido descargado de Drive a markdown."""
import io
import logging

log = logging.getLogger("eco_kb.drive.extract")

GOOGLE_DOC = "application/vnd.google-apps.document"
GOOGLE_FOLDER = "application/vnd.google-apps.folder"
PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TEXT_MIMES = {"text/markdown", "text/plain", "text/x-markdown"}


class UnsupportedFile(Exception):
    pass


def is_supported(name: str, mime: str) -> bool:
    n = name.lower()
    return (mime in (GOOGLE_DOC, PDF, DOCX) or mime in TEXT_MIMES
            or n.endswith((".md", ".pdf", ".docx")))


def extract_with_pages(name: str, mime: str, data: bytes) -> tuple[str, list[int]]:
    """(markdown, páginas de PDF sin texto extraíble). No falla si no hay texto: un PDF de imágenes devuelve ""."""
    n = name.lower()
    empty: list[int] = []
    if mime == GOOGLE_DOC or mime in TEXT_MIMES or n.endswith(".md"):
        text = data.decode("utf-8-sig")
    elif mime == PDF or n.endswith(".pdf"):
        from pypdf import PdfReader

        from eco_kb.drive.pdf_clean import clean_pdf_pages

        pages = [p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages]
        text, empty = clean_pdf_pages(pages)
    elif mime == DOCX or n.endswith(".docx"):
        import mammoth

        text = mammoth.convert_to_markdown(io.BytesIO(data)).value
    else:
        raise UnsupportedFile(f"{name} ({mime})")
    return text.strip(), empty


def extract_markdown(name: str, mime: str, data: bytes) -> str:
    text, empty = extract_with_pages(name, mime, data)
    if not text:
        raise UnsupportedFile(f"{name}: sin texto extraíble (¿PDF escaneado?)")
    if empty:
        log.warning("%s: páginas sin texto extraíble (¿imágenes?): %s", name, empty)
    return text
