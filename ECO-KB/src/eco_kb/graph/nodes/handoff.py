"""handoff_gate y handoff_response: pedido explícito de hablar con una persona (determinista, sin LLM).

El estado de la derivación (pendiente / atendida por humano) NO vive en el grafo: lo maneja la API con
ConversationStore. Acá solo se detecta el pedido y se responde con el texto de config/handoff.yaml.
"""
import re

from eco_kb.config import HandoffConfig
from eco_kb.graph.nodes.common import ai_message, audit
from eco_kb.graph.services import Services


def is_handoff_request(text: str, cfg: HandoffConfig) -> bool:
    if not cfg.enabled:
        return False
    t = text.lower()
    if any(k.lower() in t for k in cfg.keywords):
        return True
    return any(re.search(p, t) for p in cfg.patterns)


def make_handoff_gate(services: Services):
    cfg = services.config.handoff

    def handoff_gate(state: dict) -> dict:
        requested = is_handoff_request(state["message"], cfg)
        return {"handoff_requested": requested, "audit_log": audit("handoff_gate", requested=requested)}

    return handoff_gate


def make_handoff_response(services: Services):
    def handoff_response(state: dict) -> dict:
        text = services.config.handoff.response
        return {
            "final_answer": text, "intent": "handoff", "fallback_reason": None, "sources": [], "cta_url": None,
            "messages": [ai_message(text)], "audit_log": audit("handoff_response"),
        }

    return handoff_response
