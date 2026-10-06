"""Panel de administración: revisar conversaciones, calificar respuestas, atender derivaciones y gestionar la base
de conocimiento (subir archivos, revisar borradores, publicar, elegir qué tipos de cliente consultan cada uno).

Protegido con una contraseña compartida (ADMIN_TOKEN, "admin" por defecto). Vacía = panel desactivado.
"""
import base64
import binascii
import mimetypes
import secrets
from contextlib import contextmanager
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request, Response

from eco_kb.api.schemas import (DocumentRef, DocumentUpload, DocumentVisibility, DraftUpdate, HumanMessage, Review,
                                StatusChange)
from eco_kb.conversations import ConversationStore
from eco_kb.kb_documents import DocumentNotFound, DocumentStore, normalize_client_types
from eco_kb.settings import get_settings


def require_admin(authorization: str = Header(default="")) -> None:
    token = get_settings().admin_token
    if not token:
        raise HTTPException(503, "Panel desactivado: definí ADMIN_TOKEN en ECO-KB/.env y reiniciá la API.")
    given = authorization.removeprefix("Bearer ").strip()
    if not secrets.compare_digest(given.encode(), token.encode()):
        raise HTTPException(401, "Contraseña incorrecta.")


router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def _store(request: Request) -> ConversationStore:
    store = getattr(request.app.state, "conversations", None)
    if store is None:
        raise HTTPException(503, "Registro de conversaciones no disponible.")
    return store


@router.get("/ping")
def ping():
    return {"ok": True}


@router.get("/stats")
def stats(store: ConversationStore = Depends(_store)):
    return store.stats()


@router.get("/conversations")
def list_conversations(
    store: ConversationStore = Depends(_store),
    status: Literal["bot", "pending", "human"] | None = None,
    flow: Literal["support", "sales"] | None = None,
    client_type: str | None = None,
    q: str | None = Query(default=None, max_length=200),
    issues: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    return store.list(status=status, flow=flow, client_type=client_type, q=q or None, issues=issues,
                      limit=limit, offset=offset)


@router.get("/conversations/{session_id}")
def get_conversation(session_id: str, store: ConversationStore = Depends(_store)):
    conv = store.get(session_id)
    if conv is None:
        raise HTTPException(404, "Conversación no encontrada.")
    return conv


@router.post("/conversations/{session_id}/status")
def change_status(session_id: str, body: StatusChange, store: ConversationStore = Depends(_store)):
    out = store.set_status(session_id, body.status, body.author)
    if out is None:
        raise HTTPException(404, "Conversación no encontrada.")
    return out


@router.post("/conversations/{session_id}/messages")
def reply(session_id: str, body: HumanMessage, store: ConversationStore = Depends(_store)):
    """Responder como persona del equipo. Si el bot todavía la atendía, primero se toma la conversación."""
    if store.set_status(session_id, "human", body.author) is None:
        raise HTTPException(404, "Conversación no encontrada.")
    return store.add_human_message(session_id, body.content, body.author)


@router.put("/messages/{message_id}/review")
def review(message_id: int, body: Review, store: ConversationStore = Depends(_store)):
    out = store.review(message_id, body.rating, body.note, body.author)
    if out is None:
        raise HTTPException(404, "Mensaje del bot no encontrado.")
    return out


# --- base de conocimiento: archivos, borradores, publicación y visibilidad -----------------------------------
# El source_id tiene barras: va en el body (o en la query), nunca en la ruta.

def _documents(request: Request) -> DocumentStore:
    store = getattr(request.app.state, "documents", None)
    if store is None:
        raise HTTPException(503, "Gestión de archivos no disponible.")
    return store


def _changed(request: Request) -> None:
    """La KB cambió: el catálogo que usa el reescritor se recalcula en la próxima consulta."""
    for kb_store in getattr(request.app.state, "kb_stores", ()):
        kb_store.invalidate_catalog()


@contextmanager
def _errors():
    try:
        yield
    except DocumentNotFound as exc:
        raise HTTPException(404, "Archivo no encontrado.") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/documents")
def list_documents(store: DocumentStore = Depends(_documents)):
    """Los tipos salen de clients.yaml (más "common" = todos): la UI no los hardcodea."""
    return {"client_types": store.options(), "documents": store.list(), "ai": store.ai is not None}


@router.get("/documents/detail")
def get_document(audience: Literal["support", "sales"], source_id: str = Query(max_length=500),
                 store: DocumentStore = Depends(_documents)):
    doc = store.get(audience, source_id)
    if doc is None:
        raise HTTPException(404, "Archivo no encontrado.")
    return doc


@router.get("/documents/original")
def download_original(audience: Literal["support", "sales"], source_id: str = Query(max_length=500),
                      store: DocumentStore = Depends(_documents)):
    out = store.original(audience, source_id)
    if out is None:
        raise HTTPException(404, "No hay archivo original (vino del repositorio).")
    name, data = out
    media = mimetypes.guess_type(name)[0] or "application/octet-stream"
    return Response(data, media_type=media,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


@router.post("/documents/upload", status_code=202)
def upload_document(body: DocumentUpload, request: Request, background: BackgroundTasks,
                    store: DocumentStore = Depends(_documents)):
    """Guarda el archivo y lo procesa en segundo plano: si tiene buen formato se publica sin IA; si no, la IA arma
    un borrador para revisar. La vista consulta /documents hasta que `state` deja de ser `processing`."""
    try:
        data = base64.b64decode(body.content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(422, "El archivo no llegó bien (base64 inválido).") from exc
    with _errors():
        doc = store.upload(body.audience, body.filename, data, body.client_types, body.author, body.source_id)
    background.add_task(_process, store, doc["audience"], doc["source_id"], body.author,
                        getattr(request.app.state, "kb_stores", ()))
    return doc


def _process(store: DocumentStore, audience: str, source_id: str, author: str | None, kb_stores) -> None:
    store.process(audience, source_id, author)  # nunca lanza: los errores quedan en el documento
    for kb_store in kb_stores:
        kb_store.invalidate_catalog()


@router.put("/documents/draft")
def save_draft(body: DraftUpdate, store: DocumentStore = Depends(_documents)):
    """Guarda una edición como borrador. El bot sigue usando la versión publicada hasta que se publique."""
    with _errors():
        check = store.save_draft(body.audience, body.source_id, body.markdown, body.author)
    return {"document": store.get(body.audience, body.source_id), "format": check}


@router.post("/documents/publish")
def publish_document(body: DocumentRef, request: Request, store: DocumentStore = Depends(_documents)):
    """Publica el borrador: el bot empieza a usarlo al instante (con la visibilidad elegida)."""
    with _errors():
        doc = store.publish(body.audience, body.source_id, body.author)
    _changed(request)
    return doc


@router.post("/documents/discard-draft")
def discard_draft(body: DocumentRef, store: DocumentStore = Depends(_documents)):
    """Descarta el borrador (si el documento nunca se publicó, lo borra)."""
    with _errors():
        doc = store.discard_draft(body.audience, body.source_id)
    return {"document": doc, "deleted": doc is None}


@router.post("/documents/delete")
def delete_document(body: DocumentRef, request: Request, store: DocumentStore = Depends(_documents)):
    with _errors():
        store.delete(body.audience, body.source_id)
    _changed(request)
    return {"deleted": True}


@router.put("/documents/visibility")
def set_document_visibility(body: DocumentVisibility, request: Request, store: DocumentStore = Depends(_documents)):
    """Aplica al instante: actualiza el manifiesto y los fragmentos (sin re-embeber) y vacía la caché del catálogo."""
    with _errors():
        types = normalize_client_types(body.client_types, store.client_types)
        doc = store.set_visibility(body.audience, body.source_id, types, body.author)
    if doc is None:
        raise HTTPException(404, "Archivo no encontrado.")
    _changed(request)
    return doc
