"""Respuestas fijas (sin RAG ni LLM) que llevan al equipo comercial: compras y consultas técnicas en ventas.

Los textos salen de clients.yaml (messages). La URL la añade SIEMPRE el código (build_cta_url) y es la única.
"""
from eco_kb.graph.nodes.common import ai_message, audit
from eco_kb.graph.nodes.finalize import _URL, build_cta_url
from eco_kb.graph.services import Services


def _with_cta(body: str, cta_url: str) -> str:
    final = f"{body} {cta_url}"
    assert _URL.findall(final) == [cta_url], "invariante CTA violada en una respuesta fija"
    return final


def make_purchase_response(services: Services):
    """Quiere comprar / reponer / cotizar: avisa al equipo (la API marca la conversación pendiente) + CTA."""

    def purchase_response(state: dict) -> dict:
        ct = state["client_type"]
        cta_url = build_cta_url(services.config.clients[ct].cta.base_url, ct)
        final = _with_cta(services.config.messages.purchase.strip(), cta_url)
        return {
            "final_answer": final, "fallback_reason": None, "sources": [], "cta_url": cta_url,
            "handoff_requested": True, "messages": [ai_message(final)],
            "audit_log": audit("purchase_response", cta_url=cta_url),
        }

    return purchase_response


def make_sales_technical_response(services: Services):
    """Ventas nunca da contenido técnico: lo dice sin pasar por el RAG y cierra con el CTA del cliente."""

    def sales_technical_response(state: dict) -> dict:
        ct = state["client_type"]
        client = services.config.clients[ct]
        cta_url = build_cta_url(client.cta.base_url, ct)
        final = _with_cta(f"{services.config.messages.sales_technical.strip()}\n\n{client.cta.text}", cta_url)
        return {
            "final_answer": final, "fallback_reason": "technical", "sources": [], "cta_url": cta_url,
            "messages": [ai_message(final)], "audit_log": audit("sales_technical_response", cta_url=cta_url),
        }

    return sales_technical_response
