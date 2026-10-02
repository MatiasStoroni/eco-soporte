"""Panel de administración: revisar conversaciones, calificar respuestas y atender derivaciones.

Protegido con un token compartido (ADMIN_TOKEN). Sin token configurado, el panel queda desactivado.
"""
import secrets
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from eco_kb.api.schemas import HumanMessage, Review, StatusChange
from eco_kb.conversations import ConversationStore
from eco_kb.settings import get_settings


def require_admin(authorization: str = Header(default="")) -> None:
    token = get_settings().admin_token
    if not token:
        raise HTTPException(503, "Panel desactivado: definí ADMIN_TOKEN en ECO-KB/.env y reiniciá la API.")
    given = authorization.removeprefix("Bearer ").strip()
    if not secrets.compare_digest(given.encode(), token.encode()):
        raise HTTPException(401, "Token inválido.")


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
