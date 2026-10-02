import operator
from typing import Annotated, Literal, TypedDict

from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

ClientType = Literal["bodega", "hotel", "restaurante", "generic"]

# Campos que ningún nodo LLM puede tocar (los fija validate_session).
PROTECTED_FIELDS = frozenset({"is_registered", "client_type", "flow", "retrieval_filter"})


class SessionPayload(BaseModel):
    """Payload de sesión (mock): es el body de POST /chat. Fuente de verdad en cada turno."""

    session_id: str = Field(min_length=1)
    is_registered: bool
    client_type: ClientType
    language: str = "es"
    message: str = Field(min_length=1)


class RetrievedChunk(TypedDict, total=False):
    chunk_id: str
    audience: str
    source_id: str
    title: str
    section_path: str
    content: str
    client_types: list[str]
    vec_score: float | None
    rrf: float


class GraphInput(TypedDict):
    session_id: str
    is_registered: bool
    client_type: str
    language: str
    message: str


def merge_audit(old: list[dict], new: list[dict]) -> list[dict]:
    """Reducer 'add' idempotente: los subgrafos devuelven el estado completo y no deben duplicar."""
    seen = {e["id"] for e in old}
    return old + [e for e in new if e["id"] not in seen]


class GraphState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    audit_log: Annotated[list[dict], merge_audit]
    # sesión
    session_id: str
    is_registered: bool
    client_type: str
    language: str
    message: str
    flow: str
    retrieval_filter: dict
    # turno
    safety_flag: bool
    handoff_requested: bool  # el usuario pidió hablar con una persona (handoff_gate)
    intent: str  # handoff | business_question | greeting | smalltalk | capabilities | off_topic | unclear
    query: str          # intención de la pregunta, corregida y completa
    queries: list[str]  # consultas de búsqueda generadas
    docs: list[RetrievedChunk]
    relevant_docs: list[RetrievedChunk]
    attempts_ret: int
    attempts_gen: int
    answer: str
    cited_chunk_ids: list[str]
    feedback: str
    grounding_ok: bool
    answer_ok: bool
    fallback_reason: str | None
    final_answer: str
    sources: list[dict]
    cta_url: str | None
