import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from langgraph.checkpoint.postgres import PostgresSaver

from eco_kb.api.admin import router as admin_router
from eco_kb.api.schemas import ChatRequest, ChatResponse, Handoff
from eco_kb.config import load_config
from eco_kb.conversations import ConversationStore
from eco_kb.db import make_pool
from eco_kb.graph.builder import build_graph
from eco_kb.graph.services import Services
from eco_kb.ingest.restructure import GeminiRestructurer
from eco_kb.kb_documents import DocumentStore, recover_interrupted
from eco_kb.llm import GeminiEmbedder, make_chat_model
from eco_kb.retrieval.store import PgChunkStore
from eco_kb.settings import get_settings

log = logging.getLogger("eco_kb.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    cfg = load_config(s.clients_path, s.safety_path, s.handoff_path)  # falla al arrancar si falta un CTA
    admin = make_pool(s.database_url_admin, "admin", max_size=5)
    support = make_pool(s.database_url_support_ro, "support_ro")
    sales = make_pool(s.database_url_sales_ro, "sales_ro")
    checkpointer = PostgresSaver(admin)
    checkpointer.setup()
    conversations = ConversationStore(admin)
    conversations.setup()
    embedder = GeminiEmbedder(s.google_api_key, s.embedding_model, s.embedding_dim)
    # IA para reorganizar los archivos subidos que no tienen buen formato (los que sí, se publican sin IA).
    ai = GeminiRestructurer(s.google_api_key, s.llm_restructure_model or s.llm_generator_model,
                            cfg.domain.description) if s.google_api_key else None
    documents = DocumentStore(admin, list(cfg.clients), embedder=embedder, ai=ai)
    documents.setup()  # migraciones de la KB (manifiesto, RLS, gestión desde el panel); idempotentes, sin re-embeber
    recover_interrupted(admin)
    services = Services(
        generator_llm=make_chat_model(s.llm_generator_model, s.llm_generator_thinking),
        grader_llm=make_chat_model(s.llm_grader_model, s.llm_grader_thinking),
        support_store=PgChunkStore("support", support, s.top_k, s.candidate_k),
        sales_store=PgChunkStore("sales", sales, s.top_k, s.candidate_k),
        embedder=embedder,
        config=cfg, min_vector_score=s.min_vector_score, max_ret=s.max_ret, max_gen=s.max_gen,
    )
    app.state.graph = build_graph(services, checkpointer)
    app.state.handoff = cfg.handoff
    app.state.conversations = conversations
    app.state.documents = documents
    app.state.kb_stores = (services.support_store, services.sales_store)  # para invalidar el catálogo
    app.state.admin_pool = admin
    yield
    for p in (admin, support, sales):
        p.close()


app = FastAPI(title="ECO-KB", lifespan=lifespan)
app.include_router(admin_router)


@app.get("/health")
def health():
    try:
        with app.state.admin_pool.connection() as conn:
            conn.execute("SELECT 1")
    except Exception as exc:
        raise HTTPException(503, f"db: {exc}") from exc
    return {"status": "ok"}


def _safe(fn, *args, default=None):
    """El registro de conversaciones nunca debe tumbar el chat."""
    try:
        return fn(*args)
    except Exception:
        log.exception("conversations: %s falló", getattr(fn, "__name__", fn))
        return default


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    store: ConversationStore | None = getattr(app.state, "conversations", None)
    flow = "support" if req.is_registered else "sales"
    status = _safe(store.start_turn, req, default="bot") if store else "bot"

    if status == "human":  # la atiende una persona del equipo: el bot no responde
        return ChatResponse(answer="", flow=flow, intent="human_agent", sources=[], fallback_reason=None,
                            cta_url=None, audit_log=[], handoff=Handoff(status="human"))

    config = {"configurable": {"thread_id": req.session_id}, "recursion_limit": 25}
    t0 = time.monotonic()
    try:
        state = app.state.graph.invoke(req.model_dump(), config)
    except Exception:
        if store:
            _safe(store.log_system, req.session_id, "Error interno: el asistente no pudo responder este mensaje.")
        raise
    audit = state.get("audit_log", [])
    start = max((i for i, e in enumerate(audit) if e["event"] == "turn_start"), default=0)
    resp = ChatResponse(
        answer=state["final_answer"], flow=state["flow"],
        intent="safety" if state.get("fallback_reason") == "safety" else state.get("intent", "business_question"), sources=state.get("sources", []),
        fallback_reason=state.get("fallback_reason"), cta_url=state.get("cta_url"),
        audit_log=audit[start:],
    )
    if store:
        resp.message_id = _safe(store.log_bot, req.session_id, resp, int((time.monotonic() - t0) * 1000))
        resp.handoff = _handoff(store, req.session_id, status, state, resp)
    return resp


def _handoff(store: ConversationStore, session_id: str, status: str, state: dict, resp: ChatResponse) -> Handoff:
    """Derivación: pedido explícito, pedido de compra o (último recurso) N respuestas seguidas sin información."""
    cfg = app.state.handoff
    reason = None
    if state.get("handoff_requested"):
        reason = "purchase" if state.get("intent") == "purchase" else "user_request"
    elif (cfg.enabled and cfg.consecutive_fallbacks and resp.fallback_reason in cfg.fallback_reasons
          and _safe(store.trailing_fallbacks, session_id, cfg.fallback_reasons, default=0) >= cfg.consecutive_fallbacks):
        reason = "repeated_fallback"
    if reason and status == "bot" and _safe(store.request_handoff, session_id, reason, default=False):
        # El pedido explícito ya tiene su respuesta (handoff_response); el automático se avisa aparte.
        notice = cfg.notice if reason == "repeated_fallback" else None
        return Handoff(status="pending", requested_now=True, reason=reason, notice=notice)
    return Handoff(status=status)


@app.get("/chat/{session_id}/updates")
def chat_updates(session_id: str, after: int = Query(0, ge=0)):
    """Polling de la vista: mensajes del equipo y cambios de estado. El session_id (UUID) actúa de credencial."""
    store: ConversationStore | None = getattr(app.state, "conversations", None)
    out = store.updates(session_id, after) if store else None
    if out is None:
        raise HTTPException(404, "conversación no encontrada")
    return out
