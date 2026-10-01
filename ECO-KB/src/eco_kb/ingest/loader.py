"""Recorre kb/<audience>/<client_type|_common>/*.md. audience y client_types salen de la RUTA."""
import hashlib
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

from eco_kb.ingest.chunker import chunk_document

FORBIDDEN_FRONTMATTER = {"audience", "client_type", "client_types"}
AUDIENCES = ("support", "sales")

# Alias en español para carpetas (Drive o kb/ local): se normalizan a los nombres internos.
ALIASES = {"soporte": "support", "ventas": "sales", "comun": "_common", "común": "_common",
           "common": "_common", "_comun": "_common"}


def normalize_part(part: str) -> str:
    key = part.strip().lower()
    return ALIASES.get(key, key)


class IngestError(Exception):
    pass


class FrontMatter(BaseModel):
    title: str = Field(min_length=1)
    doc_type: str = Field(min_length=1)
    product: str = ""
    language: str = Field(default="es", min_length=2)


class ChunkRecord(BaseModel):
    """Todos los metadatos obligatorios deben estar presentes o el chunk se rechaza."""

    chunk_id: str = Field(min_length=1)
    audience: Literal["support", "sales"]
    source_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    section_path: str
    content: str = Field(min_length=1)
    client_types: list[str] = Field(min_length=1)
    doc_type: str = Field(min_length=1)
    product: str
    language: str
    status: Literal["published"] = "published"
    content_hash: str

    @staticmethod
    def make_hash(content: str, meta: tuple) -> str:
        return hashlib.sha256(("|".join(map(str, meta)) + "\n" + content).encode()).hexdigest()


def parse_markdown(text: str) -> tuple[dict, str]:
    """Frontmatter opcional: sin él, título = nombre del archivo y doc_type = 'documento'."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise IngestError("frontmatter sin cerrar")
    raw = yaml.safe_load(parts[1]) or {}
    if not isinstance(raw, dict):
        raise IngestError("frontmatter inválido")
    return raw, parts[2]


def derive_from_path(rel: Path, valid_client_types: set[str]) -> tuple[str, list[str]]:
    """rel = <audience>/<client_type|_common>/<file>.md"""
    if len(rel.parts) != 3:
        raise IngestError(f"ruta inválida (esperado audience/client_type/archivo.md): {rel}")
    audience, ct = normalize_part(rel.parts[0]), normalize_part(rel.parts[1])
    if audience not in AUDIENCES:
        raise IngestError(f"audience desconocida en la ruta: {audience}")
    if ct == "_common":
        return audience, ["common"]
    if ct not in valid_client_types:
        raise IngestError(f"client_type desconocido en la ruta: {ct}")
    return audience, [ct]


def load_file(path: Path, kb_dir: Path, valid_client_types: set[str]) -> list[ChunkRecord]:
    return load_text(path.relative_to(kb_dir), path.read_text(encoding="utf-8"), valid_client_types)


def load_text(rel: Path, text: str, valid_client_types: set[str]) -> list[ChunkRecord]:
    """rel = <audience>/<client_type|_common>/<nombre>; text = markdown (con o sin frontmatter)."""
    audience, client_types = derive_from_path(rel, valid_client_types)
    raw, body = parse_markdown(text)
    bad = FORBIDDEN_FRONTMATTER & set(raw)
    if bad:
        raise IngestError(f"{rel}: el frontmatter no puede definir {sorted(bad)} (se derivan de la ruta)")
    try:
        fm = FrontMatter(**{"title": rel.stem, "doc_type": "documento", **raw})
    except ValidationError as exc:
        raise IngestError(f"{rel}: frontmatter inválido: {exc}") from exc

    source_id = rel.as_posix()
    records = []
    for i, (section, content) in enumerate(chunk_document(fm.title, body)):
        meta = (audience, tuple(client_types), fm.title, section, fm.doc_type, fm.product, fm.language)
        records.append(ChunkRecord(
            chunk_id=f"{source_id}#{i}", audience=audience, source_id=source_id, title=fm.title,
            section_path=section, content=content, client_types=client_types, doc_type=fm.doc_type,
            product=fm.product, language=fm.language, content_hash=ChunkRecord.make_hash(content, meta),
        ))
    return records


def load_kb(kb_dir: Path, valid_client_types: set[str]) -> tuple[list[ChunkRecord], list[str]]:
    """Devuelve (chunks, errores). Un documento inválido se rechaza entero, el resto sigue."""
    records, errors = [], []
    for path in sorted(kb_dir.rglob("*.md")):
        if path.parent == kb_dir:  # README / notas en la raíz: no son documentos de la KB
            continue
        try:
            records += load_file(path, kb_dir, valid_client_types)
        except (IngestError, ValidationError, ValueError) as exc:
            errors.append(f"{path.relative_to(kb_dir)}: {exc}")
    return records, errors
