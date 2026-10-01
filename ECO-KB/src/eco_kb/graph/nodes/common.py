import uuid
from collections.abc import Callable

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from eco_kb.graph.state import PROTECTED_FIELDS


class ProtectedFieldError(RuntimeError):
    pass


def audit(event: str, **data) -> list[dict]:
    return [{"id": uuid.uuid4().hex, "event": event, **data}]


def guarded(name: str, fn: Callable[[dict], dict]) -> Callable[[dict], dict]:
    """Ningún nodo (salvo validate_session) puede modificar los campos protegidos."""

    def wrapper(state: dict) -> dict:
        out = fn(state)
        bad = PROTECTED_FIELDS & set(out or {})
        if bad:
            raise ProtectedFieldError(f"El nodo {name!r} intentó modificar campos protegidos: {sorted(bad)}")
        return out

    wrapper.__name__ = name
    return wrapper


def history_text(messages: list[BaseMessage], last: int = 6) -> str:
    """Historial previo (sin el último mensaje del usuario) como texto."""
    lines = []
    for m in messages[:-1][-last:]:
        role = "Usuario" if isinstance(m, HumanMessage) else "Asistente"
        lines.append(f"{role}: {m.content}")
    return "\n".join(lines)


def ai_message(text: str) -> AIMessage:
    return AIMessage(content=text)
