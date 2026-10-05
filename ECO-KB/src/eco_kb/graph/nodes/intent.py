"""classify_intent y converse: interacciones normales de conversación, siempre acotadas al negocio."""
import logging

from eco_kb.graph import prompts
from eco_kb.graph.nodes.common import ai_message, audit, history_text
from eco_kb.graph.nodes.finalize import strip_urls
from eco_kb.graph.schemas import ChatReplyOut, IntentOut
from eco_kb.graph.services import Services

log = logging.getLogger("eco_kb.intent")


def make_classify_intent(services: Services):
    llm = services.grader_llm.with_structured_output(IntentOut)
    dom = services.config.domain
    domain = prompts.domain_context(dom.description, dom.glossary)

    def classify_intent(state: dict) -> dict:
        user = f"Historial:\n{history_text(state['messages'], last=4)}\n\nÚltimo mensaje: {state['message']}"
        try:
            out: IntentOut = llm.invoke([("system", prompts.INTENT.format(domain=domain)), ("human", user)])
            intent, reason = out.intent, out.reason
        except Exception:  # si el clasificador falla, la opción segura es tratarlo como pregunta de negocio
            log.exception("classify_intent falló; se asume business_question")
            intent, reason = "business_question", "classifier_error"
        return {"intent": intent, "audit_log": audit("classify_intent", intent=intent, reason=reason)}

    return classify_intent


def canned_reply(intent: str, capabilities: str) -> str:
    """Respaldo determinista si el LLM conversacional falla."""
    cap = " ".join(capabilities.split())
    if intent == "greeting":
        return f"¡Hola! Estoy para ayudarte. {cap}"
    if intent == "smalltalk":
        return f"¡Con gusto! Si tienes alguna consulta, aquí estoy. {cap}"
    if intent == "off_topic":
        return f"Solo puedo ayudarte con temas de nuestro negocio. {cap}"
    if intent == "unclear":
        return f"¿Podrías darme más detalle sobre tu consulta? {cap}"
    return cap


def make_converse(services: Services):
    llm = services.grader_llm.with_structured_output(ChatReplyOut)
    dom = services.config.domain
    domain = prompts.domain_context(dom.description, [])  # sin glosario: evita ofrecer temas sin documentar

    def converse(state: dict) -> dict:
        intent, flow = state["intent"], state["flow"]
        capabilities = services.config.capabilities(flow, state["client_type"])
        system = prompts.CONVERSE.format(
            domain=domain, capabilities=" ".join(capabilities.split()),
            tone=services.config.clients[state["client_type"]].tone, language=state.get("language", "es"),
            intent=intent,
        )
        user = f"Historial:\n{history_text(state['messages'], last=4)}\n\nMensaje del usuario: {state['message']}"
        try:
            reply = strip_urls(llm.invoke([("system", system), ("human", user)]).reply)
            if not reply:
                raise ValueError("respuesta vacía")
        except Exception:
            log.exception("converse falló; se usa respuesta enlatada")
            reply = canned_reply(intent, capabilities)
        return {
            "final_answer": reply, "sources": [], "cta_url": None,
            "fallback_reason": "off_topic" if intent == "off_topic" else None,
            "messages": [ai_message(reply)], "audit_log": audit("converse", intent=intent),
        }

    return converse
