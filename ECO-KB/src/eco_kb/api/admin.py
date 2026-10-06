"""Panel de administración: revisar conversaciones, calificar respuestas, atender derivaciones y gestionar qué
tipos de cliente consultan cada archivo de la KB.

Protegido con una contraseña compartida (ADMIN_TOKEN, "admin" por defecto). Vacía = panel desactivado.
"""
import secrets
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from eco_kb.api.schemas import DocumentVisibility, HumanMessage, Review, StatusChange
from eco_kb.conversations import ConversationStore
from eco_kb.kb_documents import DocumentStore, normalize_client_types
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


# --- archivos de la KB: visibilidad por tipo de cliente ---------------------------------------------------

def _documents(request: Request) -> DocumentStore:
    store = getattr(request.app.state, "documents", None)
    if store is None:
        raise HTTPException(503, "Gestión de archivos no disponible.")
    return store


@router.get("/documents")
def list_documents(store: DocumentStore = Depends(_documents)):
    """Los tipos salen de clients.yaml (más "common" = todos): la UI no los hardcodea."""
    return {"client_types": store.options(), "documents": store.list()}


@router.put("/documents/visibility")
def set_document_visibility(body: DocumentVisibility, request: Request, store: DocumentStore = Depends(_documents)):
    """Aplica al instante: actualiza el manifiesto y los fragmentos (sin re-embeber) y vacía la caché del catálogo."""
    try:
        types = normalize_client_types(body.client_types, store.client_types)
        doc = store.set_visibility(body.audience, body.source_id, types, body.author)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if doc is None:
        raise HTTPException(404, "Archivo no encontrado.")
    for kb_store in getattr(request.app.state, "kb_stores", ()):
        kb_store.invalidate_catalog()
    return doc
