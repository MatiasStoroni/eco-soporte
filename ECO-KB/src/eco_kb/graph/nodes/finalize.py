"""no_answer, finalize_support, finalize_sales."""
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from eco_kb.graph.nodes.common import ai_message, audit
from eco_kb.graph.services import Services

_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)


def strip_urls(text: str) -> str:
    return re.sub(r"[ \t]{2,}", " ", _URL.sub("", text)).strip()


def build_cta_url(base_url: str, client_type: str) -> str:
    parts = urlsplit(base_url)
    query = parse_qsl(parts.query) + [
        ("utm_source", "eco_kb"), ("utm_medium", "chatbot"), ("utm_campaign", client_type),
    ]
    return urlunsplit(parts._replace(query=urlencode(query)))


def make_no_answer():
    def no_answer(state: dict) -> dict:
        if not state.get("relevant_docs"):
            reason = "no_documents"
        elif not state.get("grounding_ok"):
            reason = "ungrounded"
        else:
            reason = "answer_mismatch"
        return {"fallback_reason": reason, "audit_log": audit("no_answer", reason=reason)}

    return no_answer


def _sources(state: dict) -> list[dict]:
    by_id = {d["chunk_id"]: d for d in state.get("relevant_docs", [])}
    seen, out = set(), []
    for cid in state.get("cited_chunk_ids", []):
        d = by_id.get(cid)
        if d and (key := (d["title"], d.get("section_path", ""))) not in seen:
            seen.add(key)
            out.append({"title": d["title"], "section": d.get("section_path", "")})
    return out


def make_finalize_support(services: Services):
    def finalize_support(state: dict) -> dict:
        if state.get("fallback_reason"):
            text, sources = services.config.clients[state["client_type"]].fallback.support, []
        else:
            text, sources = state["answer"], _sources(state)
        return {"final_answer": text, "sources": sources, "cta_url": None,
                "messages": [ai_message(text)], "audit_log": audit("finalize_support")}

    return finalize_support


def make_finalize_sales(services: Services):
    def finalize_sales(state: dict) -> dict:
        ct = state["client_type"]
        client = services.config.clients[ct]
        if state.get("fallback_reason"):
            body, sources = client.fallback.sales, []
        else:
            body, sources = strip_urls(state["answer"]), _sources(state)
        cta_url = build_cta_url(client.cta.base_url, ct)
        final = f"{body}\n\n{client.cta.text} {cta_url}"
        # Invariante: el CTA está y es la ÚNICA URL de la respuesta.
        assert _URL.findall(final) == [cta_url], "invariante CTA violada en finalize_sales"
        return {"final_answer": final, "sources": sources, "cta_url": cta_url,
                "messages": [ai_message(final)], "audit_log": audit("finalize_sales", cta_url=cta_url)}

    return finalize_sales
