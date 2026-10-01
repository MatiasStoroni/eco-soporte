import re

from eco_kb.config import SafetyConfig
from eco_kb.graph.nodes.common import ai_message, audit
from eco_kb.graph.services import Services


def is_safety_query(text: str, cfg: SafetyConfig) -> bool:
    t = text.lower()
    if any(k.lower() in t for k in cfg.keywords):
        return True
    return any(re.search(p, t) for p in cfg.patterns)


def make_safety_gate(services: Services):
    cfg = services.config.safety

    def safety_gate(state: dict) -> dict:
        flagged = is_safety_query(state["message"], cfg)
        return {"safety_flag": flagged, "audit_log": audit("safety_gate", flagged=flagged)}

    return safety_gate


def make_safety_response(services: Services):
    def safety_response(state: dict) -> dict:
        text = services.config.safety.response
        return {
            "final_answer": text, "fallback_reason": "safety", "sources": [], "cta_url": None,
            "messages": [ai_message(text)], "audit_log": audit("safety_response"),
        }

    return safety_response
