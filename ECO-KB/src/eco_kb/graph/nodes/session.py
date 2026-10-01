from langchain_core.messages import HumanMessage

from eco_kb.graph.nodes.common import audit
from eco_kb.graph.services import Services
from eco_kb.retrieval.filters import build_filter


def make_validate_session(services: Services):
    def validate_session(state: dict) -> dict:
        """Única fuente de flow / retrieval_filter. Resetea los campos del turno."""
        client_type = state["client_type"]
        if client_type not in services.config.clients:
            raise ValueError(f"client_type no permitido: {client_type!r}")
        flow = "support" if state["is_registered"] else "sales"
        lang = state.get("language") or "es"
        f = build_filter(flow, client_type, lang)
        return {
            "flow": flow,
            "retrieval_filter": f.to_dict(),
            "messages": [HumanMessage(content=state["message"])],
            "safety_flag": False, "intent": "business_question", "query": "", "queries": [], "docs": [], "relevant_docs": [],
            "attempts_ret": 0, "attempts_gen": 0, "answer": "", "cited_chunk_ids": [],
            "feedback": "", "grounding_ok": False, "answer_ok": False,
            "fallback_reason": None, "final_answer": "", "sources": [], "cta_url": None,
            "audit_log": audit("turn_start", flow=flow, client_type=client_type,
                               filter=f.to_dict()),
        }

    return validate_session
