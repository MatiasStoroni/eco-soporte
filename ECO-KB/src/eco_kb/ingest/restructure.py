"""Prepara documentos para la KB: los que ya tienen buen formato se usan tal cual; el resto lo reformatea la IA.

    python -m uv run python -m eco_kb.ingest.restructure <archivos o carpetas> --audiencia soporte|ventas
        [--carpeta borradores] [--destino docs/estructurados] [--solo-validar] [--forzar]

Por cada archivo (.md, .pdf, .docx), en este orden y gastando IA solo cuando hace falta:

1. Si el destino ya existe y se generó desde esta misma fuente (`fuente_sha256`), se saltea.
2. Se extrae el texto y se corre `check_format` (sin IA). Si no hay problemas, se copia TAL CUAL.
3. Si hay problemas, Gemini lo reorganiza (lee el PDF entero, también las páginas que son imágenes) y otra
   llamada verifica el borrador contra el original. Además se comparan las cifras sin IA en los dos sentidos.
   El borrador va con un `<nombre>.revision.txt` que lista lo que hay que mirar.

Los borradores van a `<destino>/<audiencia>/<carpeta>/`. Con la carpeta por defecto (`borradores`), que no es un
tipo de cliente, la ingesta los carga SIN HABILITAR: el bot no los usa hasta que alguien los revisa y los tilda en
el panel. Un borrador editado a mano nunca se pisa (salvo con --forzar).

La IA solo reorganiza: no agrega, no resume y no corrige datos. Lo ilegible lo deja como pendiente.
"""
import argparse
import hashlib
import logging
import mimetypes
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import yaml
from pydantic import BaseModel, Field

from eco_kb.drive.extract import DOCX, PDF, extract_with_pages
from eco_kb.graph.nodes.generation import check_numeric_claims
from eco_kb.ingest.format_check import check_format
from eco_kb.ingest.loader import IngestError, load_text, parse_markdown

log = logging.getLogger("eco_kb.restructure")

SUPPORTED = (".md", ".pdf", ".docx")
AUDIENCES = ("soporte", "ventas")
_DOCX_IMAGE = re.compile(r"!\[[^\]]*\]\(data:[^)]*\)")
_INNER_HEADING = re.compile(r"^#{1,2}(\s)", re.MULTILINE)
_DATE = re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b")


def sha256(data: bytes | str) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


# --- salida estructurada de la IA ---------------------------------------------------------------------------

class Seccion(BaseModel):
    titulo: str = Field(description="Título descriptivo del tema, con el nombre del producto si corresponde.")
    contenido: str = Field(description="Markdown del tema: párrafos, listas o tablas. Sin títulos # ni ##.")
    desde_imagen: bool = Field(default=False, description="True si el contenido sale de una imagen del original.")


class Borrador(BaseModel):
    titulo: str = Field(description="Título del documento: producto + tipo (p. ej. 'X4 Desincrustante - Ficha técnica').")
    producto: str = Field(default="", description="Producto principal, tal como figura en el original.")
    tipo_documento: str = Field(default="documento", description="ficha_tecnica, manual, procedimiento, comercial…")
    secciones: list[Seccion] = Field(min_length=1)
    pendientes: list[str] = Field(default_factory=list,
                                  description="Lo ilegible, ambiguo o contradictorio del original, para que lo revise una persona.")


class Observacion(BaseModel):
    seccion: str
    texto: str = Field(description="El fragmento del borrador observado.")
    problema: str = Field(description="Qué difiere del original: agregado, cambiado u omitido.")


class Verificacion(BaseModel):
    observaciones: list[Observacion] = Field(default_factory=list)


SYSTEM_RESTRUCTURE = """Reorganizás documentos de una empresa de higiene profesional para una base de conocimiento
que consulta un asistente (RAG). {domain}

Tu trabajo es SOLO de formato y orden. Reglas:
1. No agregues información que no esté en el original. No completes, no expliques, no supongas.
2. No resumas ni omitas datos: tienen que estar todas las cifras, unidades, diluciones, tiempos, temperaturas,
   códigos, advertencias y nombres de productos del original, con el mismo valor.
3. Podés unificar cómo se escriben las cifras (`20 ml/L`, `2 %`, `5-10 min`) sin cambiar su valor.
4. Una sección por tema, con un título descriptivo que nombre el producto (p. ej. "Dilución de X4 para baños",
   no "Uso" ni "Información"). Pasos en listas numeradas; tablas en markdown si ayudan.
5. Descartá lo que no es contenido: encabezados y pies repetidos, índices, números de página, decoración.
6. Leé también el texto de las imágenes (tablas, instrucciones, diagramas con texto) y marcá esas secciones
   con desde_imagen=true. Si algo no se lee bien, no lo adivines: anotalo en `pendientes`.
7. Anotá en `pendientes` las contradicciones o ambigüedades del original, sin resolverlas.
{audience}"""

AUDIENCE_RULES = {
    "soporte": "Es documentación de SOPORTE (técnica, para clientes que ya compraron).",
    "ventas": ("Es documentación de VENTAS (comercial, para prospectos). Si el original trae dosis, diluciones o "
               "procedimientos, conservalos igual pero anotá en `pendientes` que ese contenido técnico debería ir "
               "en un documento de soporte."),
}

SYSTEM_VERIFY = """Compará un BORRADOR con el documento ORIGINAL del que salió. El borrador solo debía reorganizar
el original, sin agregar, cambiar ni omitir datos. Listá cada fragmento del borrador que:
- agrega algo que no está en el original;
- cambia un dato (cifra, unidad, tiempo, producto, advertencia, orden de pasos que cambie el sentido);
y cada dato importante del original que falta en el borrador (en ese caso, `texto` = el dato del original).
NO son problemas: reformular con otras palabras, unificar el formato de las cifras, cambiar el orden de las
secciones, ni omitir lo que no es contenido (encabezados y pies de página, logos, marcas o eslóganes decorativos,
códigos QR, direcciones, mails y webs de contacto, números de página, fechas o números de revisión).
Si no hay problemas, devolvé la lista vacía."""


# --- fuente -------------------------------------------------------------------------------------------------

@dataclass
class Source:
    path: Path
    data: bytes
    mime: str
    text: str
    empty_pages: list[int] = field(default_factory=list)
    images: int = 0  # imágenes de un DOCX (la IA no las recibe: se avisa)

    @property
    def sha(self) -> str:
        return sha256(self.data)

    @property
    def is_pdf(self) -> bool:
        return self.mime == PDF


def source_from_bytes(name: str, data: bytes) -> Source:
    ext = Path(name).suffix.lower()
    if ext not in SUPPORTED:
        raise ValueError(f"formato no soportado: {name} (se aceptan {', '.join(SUPPORTED)})")
    mime = PDF if ext == ".pdf" else DOCX if ext == ".docx" else mimetypes.guess_type(name)[0] or "text/markdown"
    text, empty = extract_with_pages(name, mime, data)
    text = text.replace("\r\n", "\n")  # archivos con CRLF (Windows): el hash del borrador no debe depender de eso
    images = 0
    if ext == ".docx":  # mammoth incrusta las imágenes como data URI: ruido para el texto
        images = len(_DOCX_IMAGE.findall(text))
        text = _DOCX_IMAGE.sub("", text).strip()
    return Source(Path(name), data, mime, text, empty, images)


def load_source(path: Path) -> Source:
    src = source_from_bytes(path.name, path.read_bytes())
    src.path = path
    return src


# --- IA -----------------------------------------------------------------------------------------------------

class Restructurer(Protocol):
    def restructure(self, src: Source, audience: str) -> Borrador: ...
    def verify(self, src: Source, draft: str) -> list[Observacion]: ...


class GeminiRestructurer:
    """Dos llamadas por documento: reorganizar y verificar. El PDF va entero (Gemini lee las imágenes)."""

    def __init__(self, api_key: str, model: str, domain: str = "", retries: int = 4):
        from google import genai

        self._client = genai.Client(api_key=api_key or None)
        self._model = model.removeprefix("google_genai:")
        self._domain, self._retries = domain, retries

    def _original(self, src: Source) -> list:
        from google.genai import types

        if src.is_pdf:
            return [types.Part.from_bytes(data=src.data, mime_type=PDF)]
        return [f"DOCUMENTO ORIGINAL ({src.path.name}):\n\n{src.text}"]

    def _call(self, system: str, contents: list, schema: type[BaseModel]):
        from google.genai import errors, types

        cfg = types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json",
                                          response_schema=schema)
        for attempt in range(self._retries + 1):
            try:
                resp = self._client.models.generate_content(model=self._model, contents=contents, config=cfg)
                return resp.parsed if isinstance(resp.parsed, schema) else schema.model_validate_json(resp.text)
            except errors.APIError as exc:
                if exc.code not in (429, 500, 503) or attempt == self._retries:
                    raise
                log.warning("Gemini %s, reintento en %ss", exc.code, 2**attempt)
                time.sleep(2**attempt)
        raise RuntimeError("unreachable")

    def restructure(self, src: Source, audience: str) -> Borrador:
        system = SYSTEM_RESTRUCTURE.format(domain=self._domain, audience=AUDIENCE_RULES[audience])
        return self._call(system, [*self._original(src), "Reorganizá este documento según las reglas."], Borrador)

    def verify(self, src: Source, draft: str) -> list[Observacion]:
        out = self._call(SYSTEM_VERIFY, [*self._original(src), f"BORRADOR:\n\n{draft}"], Verificacion)
        return out.observaciones


# --- markdown de salida --------------------------------------------------------------------------------------

def _frontmatter(meta: dict, body: str) -> str:
    return "---\n" + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False, width=1000) + "---\n\n" + body + "\n"


PROVENANCE_KEYS = ("fuente", "fuente_sha256", "borrador_sha256")


def without_provenance(md: str) -> str:
    """Sin los datos de procedencia del frontmatter: solo los usa el comando para no reprocesar (en el panel, el
    original y su hash viven en la base) y en el editor son ruido."""
    raw, body = parse_markdown(md)
    meta = {k: v for k, v in raw.items() if k not in PROVENANCE_KEYS}
    return _frontmatter(meta, body.strip()) if meta else body.strip() + "\n"


def with_provenance(text: str, src: Source) -> str:
    """El documento tal cual, con la procedencia en el frontmatter (para no volver a procesarlo)."""
    raw, body = parse_markdown(text)
    body = body.strip()
    meta = {**raw, "fuente": src.path.name, "fuente_sha256": src.sha, "borrador_sha256": sha256(body)}
    return _frontmatter(meta, body)


def _section(s: Seccion) -> str:
    # Un # o ## dentro del contenido partiría la sección en el chunker: se baja a ###.
    content = _INNER_HEADING.sub(r"###\1", s.contenido.strip())
    return f"## {s.titulo.strip()}\n{content}"


def render(b: Borrador, src: Source) -> str:
    body = "\n\n".join(_section(s) for s in b.secciones)
    meta = {"title": b.titulo.strip(), "doc_type": b.tipo_documento or "documento", "product": b.producto,
            "language": "es", "fuente": src.path.name, "fuente_sha256": src.sha, "borrador_sha256": sha256(body)}
    return _frontmatter(meta, body)


class NeedsAI(Exception):
    """El documento no tiene buen formato y no hay IA configurada (falta GOOGLE_API_KEY)."""


@dataclass
class Prepared:
    markdown: str
    used_ai: bool
    review: dict  # {used_ai, reason, alerts, blocks: [{title, items, alert}]}: lo que muestra el panel

    @property
    def alerts(self) -> int:
        return self.review["alerts"]


def _block(title: str, items: list[str], alert: bool = False) -> dict | None:
    return {"title": title, "items": items, "alert": alert} if items else None


def prepare(src: Source, audience: str, ai: Restructurer | None, valid_client_types: set[str],
            rel: Path | None = None) -> Prepared:
    """Fuente → markdown listo para la KB. Sin IA si el formato ya es válido. `rel` = ruta para validar la carga."""
    fmt = check_format(src.text, src.empty_pages)
    if fmt.ok:
        blocks = [_block("Avisos de formato (no impiden publicarlo)", fmt.warnings)]
        return Prepared(with_provenance(src.text, src), False,
                        {"used_ai": False, "reason": [], "alerts": 0, "blocks": [x for x in blocks if x]})
    if ai is None:
        raise NeedsAI("; ".join(fmt.problems))

    b = ai.restructure(src, audience)
    md = render(b, src)
    body = parse_markdown(md)[1]
    draft_fmt = check_format(md)
    # Cifras agregadas: solo se pueden comparar las secciones que salen de texto (las de imágenes no están en el
    # texto extraíble y se listan aparte para revisarlas a mano). Cifras perdidas: contra todo el borrador.
    from_text = "\n\n".join(_section(s) for s in b.secciones if not s.desde_imagen)
    original = _DATE.sub(" ", src.text)  # "23/09/2026" no es una proporción (y la fecha de revisión se descarta)
    added = check_numeric_claims(_DATE.sub(" ", from_text), [original])
    lost = check_numeric_claims(original, [body])
    obs = ai.verify(src, md)
    ingest_error = None
    try:
        load_text(rel or Path(audience, "borradores", f"{src.path.stem}.md"), md, valid_client_types)
    except (IngestError, ValueError) as exc:
        ingest_error = str(exc)

    blocks = [
        _block("No se puede cargar en la KB", [ingest_error] if ingest_error else [], True),
        _block("Cifras que no están en el texto del original (¿inventadas o mal copiadas?)", added, True),
        _block("Cifras del original que no aparecen en el borrador", lost, True),
        _block("Observaciones del verificador", [f"[{o.seccion}] «{o.texto}»: {o.problema}" for o in obs], True),
        _block("Problemas de formato del borrador", draft_fmt.problems, True),
        _block("Secciones que salen de imágenes: sus cifras no se pueden chequear solas, compararlas con el original",
               [s.titulo for s in b.secciones if s.desde_imagen]),
        _block("Pendientes que marcó la IA (ilegible, ambiguo o contradictorio)", b.pendientes),
        _block("Avisos de formato", draft_fmt.warnings),
        _block("Imágenes del DOCX", [f"El original tiene {src.images} imágenes que la IA no leyó: si tienen texto, "
                                     "transcribilo a mano."] if src.images else []),
    ]
    blocks = [x for x in blocks if x]
    alerts = sum(len(x["items"]) for x in blocks if x["alert"])
    return Prepared(md, True, {"used_ai": True, "reason": fmt.problems, "alerts": alerts, "blocks": blocks})


def review_text(review: dict, source_name: str) -> str:
    """El <nombre>.revision.txt del comando (el panel muestra lo mismo)."""
    n = review["alerts"]
    lines = [
        f"Borrador generado con IA a partir de «{source_name}» ({time.strftime('%Y-%m-%d %H:%M')}).",
        f"Estado: {'REVISAR: ' + str(n) + ' alertas automáticas' if n else 'sin alertas automáticas'}"
        " (igual hay que leerlo entero contra el original).",
        "",
        "Antes de publicarlo: compará con el original y corregí lo necesario en el .md (una vez editado, este",
        "comando no lo vuelve a pisar). Después, movelo a su carpeta final o dejalo acá, volvé a ingerir y",
        "tildá en el panel (Archivos) qué clientes lo consultan. Este .txt no se ingiere: borralo al terminar.",
    ]
    for blk in review["blocks"]:
        lines.extend(["", f"## {blk['title']}", *[f"- {i}" for i in blk["items"]]])
    return "\n".join(lines) + "\n"


# --- un archivo (comando) -----------------------------------------------------------------------------------

@dataclass
class Result:
    path: Path
    action: str          # skipped | kept | ai | invalid | protected | error
    detail: str = ""
    output: Path | None = None
    alerts: int = 0


def process(path: Path, audience: str, out_dir: Path, ai: Restructurer | None, valid_client_types: set[str],
            force: bool = False, only_check: bool = False) -> Result:
    out = out_dir / f"{path.stem}.md"
    report_path = out_dir / f"{path.stem}.revision.txt"
    src = load_source(path)
    same_file = out.resolve() == path.resolve()

    if out.exists() and not same_file and not force and not only_check:
        raw, body = parse_markdown(out.read_text(encoding="utf-8"))
        if raw.get("fuente_sha256") == src.sha:
            return Result(path, "skipped", "ya procesado y la fuente no cambió", out)
        if raw.get("borrador_sha256") != sha256(body.strip()):
            return Result(path, "protected", "el destino existe y fue editado a mano (o no lo generó este "
                          "comando): no se pisa. Usá --forzar para regenerarlo", out)

    try:
        prep = prepare(src, audience, None if only_check else ai, valid_client_types,
                       Path(audience, out_dir.name, out.name))
    except NeedsAI as exc:
        if only_check:
            return Result(path, "invalid", str(exc))
        return Result(path, "error", "hace falta IA y no hay GOOGLE_API_KEY")
    if not prep.used_ai:
        if not same_file and not only_check:
            out_dir.mkdir(parents=True, exist_ok=True)
            out.write_text(prep.markdown, encoding="utf-8")
            report_path.unlink(missing_ok=True)
        warnings = [i for blk in prep.review["blocks"] for i in blk["items"]]
        extra = f" (avisos: {'; '.join(warnings)})" if warnings else ""
        return Result(path, "kept", f"formato válido: se usa tal cual, sin IA{extra}",
                      None if same_file or only_check else out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out.write_text(prep.markdown, encoding="utf-8")
    report_path.write_text(review_text(prep.review, src.path.name), encoding="utf-8")
    return Result(path, "ai", f"reformateado con IA ({'; '.join(prep.review['reason'])})", out, prep.alerts)


def collect(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            files += sorted(f for f in p.rglob("*") if f.suffix.lower() in SUPPORTED)
        elif p.suffix.lower() in SUPPORTED:
            files.append(p)
        else:
            log.warning("OMITIDO %s: formato no soportado (%s)", p, ", ".join(SUPPORTED))
    return files


_LABELS = {"skipped": "SIN CAMBIOS", "kept": "TAL CUAL", "ai": "CON IA", "invalid": "NECESITA IA",
           "protected": "PROTEGIDO", "error": "ERROR"}


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for noisy in ("httpx", "google_genai", "google_genai.models"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("paths", nargs="+", help="archivos (.md, .pdf, .docx) o carpetas")
    p.add_argument("--audiencia", choices=AUDIENCES, required=True,
                   help="soporte (técnico, clientes) o ventas (comercial, prospectos)")
    p.add_argument("--carpeta", default="borradores",
                   help="subcarpeta de destino. 'borradores' (u otra que no sea un tipo de cliente) entra sin habilitar")
    p.add_argument("--destino", default="docs/estructurados", help="raíz de la KB")
    p.add_argument("--solo-validar", action="store_true", help="no escribe nada ni usa IA: solo dice qué haría falta")
    p.add_argument("--forzar", action="store_true", help="reprocesa aunque no haya cambios o el borrador esté editado")
    args = p.parse_args(argv)

    from eco_kb.config import load_config
    from eco_kb.settings import get_settings

    s = get_settings()
    cfg = load_config(s.clients_path, s.safety_path)
    ai = None
    if s.google_api_key and not args.solo_validar:
        ai = GeminiRestructurer(s.google_api_key, s.llm_restructure_model or s.llm_generator_model,
                                cfg.domain.description)
    out_dir = Path(args.destino) / args.audiencia / args.carpeta
    files = collect(args.paths)
    if not files:
        log.error("No hay archivos para procesar.")
        return 2

    results = []
    for f in files:
        try:
            r = process(f, args.audiencia, out_dir, ai, set(cfg.clients), args.forzar, args.solo_validar)
        except Exception as exc:  # un archivo malo no frena al resto
            log.exception("falló %s", f)
            r = Result(f, "error", str(exc))
        results.append(r)
        where = f" -> {r.output}" if r.output else ""
        alerts = f" [{r.alerts} alertas: ver .revision.txt]" if r.alerts else ""
        log.info("%-11s %s%s: %s%s", _LABELS[r.action], f.name, where, r.detail, alerts)

    used_ai = sum(r.action == "ai" for r in results)
    log.info("\n%d archivos: %d con IA, %d tal cual, %d sin cambios, %d protegidos, %d con error.",
             len(results), used_ai, sum(r.action == "kept" for r in results), sum(r.action == "skipped" for r in results),
             sum(r.action == "protected" for r in results), sum(r.action == "error" for r in results))
    if used_ai:
        log.info("Revisá cada borrador con su .revision.txt antes de habilitarlo en el panel.")
    return 1 if any(r.action == "error" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
