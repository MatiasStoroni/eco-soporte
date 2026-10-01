from typing import Literal

from pydantic import BaseModel, Field

Intent = Literal["business_question", "greeting", "smalltalk", "capabilities", "off_topic", "unclear"]


class IntentOut(BaseModel):
    intent: Intent
    reason: str = ""


class ChatReplyOut(BaseModel):
    reply: str = Field(description="Respuesta breve (2-3 frases) que termina llevando la charla al negocio")


class RewriteOut(BaseModel):
    intent: str = Field(description="La pregunta real del usuario, corregida y completa")
    queries: list[str] = Field(
        description="2 a 4 consultas de búsqueda distintas (pregunta clara, palabras clave/sinónimos, frase tipo documento)"
    )


class ChunkGrade(BaseModel):
    chunk_id: str
    relevant: bool


class GradeOut(BaseModel):
    grades: list[ChunkGrade]


class GenerationOut(BaseModel):
    answer: str = Field(description="Respuesta usando SOLO el contexto")
    cited_chunk_ids: list[str] = Field(description="chunk_id de los fragmentos realmente usados")


class GroundingOut(BaseModel):
    grounded: bool = Field(description="True si TODA afirmación está respaldada por los fragmentos citados")
    unsupported_claims: list[str] = Field(default_factory=list)


class AnswerCheckOut(BaseModel):
    answers_question: bool
    reason: str = ""
