"""rewrite_query, retrieve, grade_documents."""
import logging

from eco_kb.graph import prompts
from eco_kb.graph.nodes.common import audit, history_text
from eco_kb.graph.schemas import GradeOut, RewriteOut
from eco_kb.graph.services import Services
from eco_kb.retrieval.filters import RetrievalFilter, violates_filter
from eco_kb.retrieval.store import RRF_K

log = logging.getLogger("eco_kb.security")

FUSED_TOP = 10
MAX_QUERIES = 4


def format_catalog(catalog: list[dict]) -> str:
    lines = []
    for d in catalog:
        prod = f" · {d['product']}" if d.get("product") else ""
        secs = "; ".join(d.get("sections", []))
        lines.append(f"- {d['title']}{prod} · {secs}")
    return "\n".join(lines) or "(vacío)"


def fuse_chunk_lists(lists: list[list[dict]], top_n: int = FUSED_TOP) -> list[dict]:
    """RRF entre los resultados de varias consultas. Conserva el mejor vec_score de cada chunk."""
    fused: dict[str, dict] = {}
    for chunks in lists:
        for rank, c in enumerate(chunks, start=1):
            item = fused.setdefault(c["chunk_id"], {**c, "rrf": 0.0})
            item["rrf"] += 1.0 / (RRF_K + rank)
            scores = [s for s in (item.get("vec_score"), c.get("vec_score")) if s is not None]
            item["vec_score"] = max(scores) if scores else None
    return sorted(fused.values(), key=lambda c: c["rrf"], reverse=True)[:top_n]


def make_rewrite_query(services: Services, store):
    llm = services.grader_llm.with_structured_output(RewriteOut)
    domain = services.config.domain

    def rewrite_query(state: dict) -> dict:
        f = RetrievalFilter.from_dict(state["retrieval_filter"])
        retry = state.get("attempts_ret", 0) > 0
        hint = (
            f"IMPORTANTE: el intento anterior (consultas {state.get('queries')}) no encontró documentos "
            "relevantes: reformula con otros términos, sinónimos y más generales." if retry else ""
        )
        system = prompts.REWRITE.format(
            domain=prompts.domain_context(domain.description, domain.glossary),
            catalog=format_catalog(store.catalog(f)), language=state.get("language", "es"), hint=hint,
        )
        user = f"Historial:\n{history_text(state['messages'])}\n\nÚltimo mensaje del usuario: {state['message']}"
        out: RewriteOut = llm.invoke([("system", system), ("human", user)])
        queries = [q.strip() for q in out.queries if q.strip()][:MAX_QUERIES] or [out.intent]
        return {"query": out.intent, "queries": queries,
                "audit_log": audit("rewrite_query", intent=out.intent, queries=queries, retry=retry)}

    return rewrite_query


def make_retrieve(services: Services, store):
    def retrieve(state: dict) -> dict:
        f = RetrievalFilter.from_dict(state["retrieval_filter"])
        queries = state.get("queries") or [state.get("query") or state["message"]]
        # la consulta original del usuario también cuenta: protege contra reescrituras que se desvíen
        queries = [*queries, state["message"]] if state["message"] not in queries else queries
        vectors = services.embedder.embed_queries(queries)
        lists = [store.search(v, q, f) for v, q in zip(vectors, queries, strict=True)]
        chunks = fuse_chunk_lists(lists)
        ok = [c for c in chunks if not violates_filter(c, f)]
        events = audit("retrieve", queries=queries, chunk_ids=[c["chunk_id"] for c in ok])
        if len(ok) != len(chunks):
            log.error("SECURITY: %d chunks descartados post-recuperación", len(chunks) - len(ok))
            events += audit("security_event", dropped=len(chunks) - len(ok))
        return {"docs": ok, "attempts_ret": state.get("attempts_ret", 0) + 1, "audit_log": events}

    return retrieve


def make_grade_documents(services: Services):
    llm = services.grader_llm.with_structured_output(GradeOut)
    dom = services.config.domain
    domain = prompts.domain_context(dom.description, dom.glossary)

    def grade_documents(state: dict) -> dict:
        # 1) umbral de score vectorial (los hits solo-FTS no tienen vec_score y pasan a la fase LLM)
        candidates = [
            d for d in state.get("docs", [])
            if d.get("vec_score") is None or d["vec_score"] >= services.min_vector_score
        ]
        relevant: list[dict] = []
        if candidates:
            # 2) una única llamada en lote
            body = "\n\n".join(f"[{d['chunk_id']}]\n{d['content']}" for d in candidates)
            out: GradeOut = llm.invoke([
                ("system", prompts.GRADE.format(domain=domain)),
                ("human", f"Pregunta del usuario: {state['message']}\nPregunta interpretada: {state['query']}"
                          f"\n\nFragmentos:\n{body}"),
            ])
            ok_ids = {g.chunk_id for g in out.grades if g.relevant}
            relevant = [d for d in candidates if d["chunk_id"] in ok_ids]
        return {
            "relevant_docs": relevant,
            "audit_log": audit("grade_documents", candidates=len(candidates),
                               relevant_ids=[d["chunk_id"] for d in relevant]),
        }

    return grade_documents
