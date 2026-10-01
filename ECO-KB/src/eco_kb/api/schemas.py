from pydantic import BaseModel

from eco_kb.graph.state import SessionPayload

# El body de POST /chat ES el payload de sesión (mock de la futura sesión real).
ChatRequest = SessionPayload


class Source(BaseModel):
    title: str
    section: str


class ChatResponse(BaseModel):
    answer: str
    flow: str
    intent: str  # business_question | greeting | smalltalk | capabilities | off_topic | unclear | safety
    sources: list[Source]
    fallback_reason: str | None
    cta_url: str | None
    audit_log: list[dict]
