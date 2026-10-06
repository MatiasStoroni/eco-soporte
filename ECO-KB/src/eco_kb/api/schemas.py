from typing import Literal

from pydantic import BaseModel, Field

from eco_kb.graph.state import SessionPayload

# El body de POST /chat ES el payload de sesión (mock de la futura sesión real).
ChatRequest = SessionPayload


class Source(BaseModel):
    title: str
    section: str


class Handoff(BaseModel):
    status: Literal["bot", "pending", "human"]  # human = el bot no responde, atiende el equipo
    requested_now: bool = False                   # este turno disparó la derivación
    reason: str | None = None                     # user_request | purchase | repeated_fallback
    notice: str | None = None                     # aviso para mostrar en la vista (no es parte de `answer`)


class ChatResponse(BaseModel):
    answer: str  # vacío si la conversación la atiende una persona (handoff.status == "human")
    flow: str
    intent: str  # business_question | technical_question | purchase | greeting | smalltalk | capabilities | off_topic | unclear | safety | handoff | human_agent
    sources: list[Source]
    fallback_reason: str | None
    cta_url: str | None
    audit_log: list[dict]
    handoff: Handoff | None = None
    message_id: int | None = None  # id del mensaje registrado (para el panel)


class StatusChange(BaseModel):
    status: Literal["bot", "human"]  # tomar la conversación o devolverla al bot (también descarta la derivación)
    author: str | None = Field(default=None, max_length=80)


class HumanMessage(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    author: str | None = Field(default=None, max_length=80)


class Review(BaseModel):
    rating: Literal["good", "bad"] | None
    note: str | None = Field(default=None, max_length=2000)
    author: str | None = Field(default=None, max_length=80)


class DocumentVisibility(BaseModel):
    """Qué tipos de cliente consultan un archivo. El source_id va en el body porque tiene barras."""
    audience: Literal["support", "sales"]
    source_id: str = Field(min_length=1, max_length=500)
    client_types: list[str] = Field(max_length=20)  # [] = sin habilitar; ["common"] = todos
    author: str | None = Field(default=None, max_length=80)


class DocumentRef(BaseModel):
    audience: Literal["support", "sales"]
    source_id: str = Field(min_length=1, max_length=500)
    author: str | None = Field(default=None, max_length=80)


class DocumentUpload(BaseModel):
    """El archivo va en base64 (sin multipart, para no sumar dependencias). Límite: MAX_UPLOAD_BYTES."""
    audience: Literal["support", "sales"]
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1, max_length=21_000_000)
    client_types: list[str] = Field(default_factory=list, max_length=20)  # solo para documentos nuevos
    source_id: str | None = Field(default=None, max_length=500)            # reemplazar ese documento
    author: str | None = Field(default=None, max_length=80)


class DraftUpdate(DocumentRef):
    markdown: str = Field(min_length=1, max_length=2_000_000)
