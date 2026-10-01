import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from langgraph.checkpoint.postgres import PostgresSaver

from eco_kb.api.schemas import ChatRequest, ChatResponse
from eco_kb.config import load_config
from eco_kb.db import make_pool
from eco_kb.graph.builder import build_graph
from eco_kb.graph.services import Services
from eco_kb.llm import GeminiEmbedder, make_chat_model
from eco_kb.retrieval.store import PgChunkStore
from eco_kb.settings import get_settings

log = logging.getLogger("eco_kb.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    cfg = load_config(s.clients_path, s.safety_path)  # falla al arrancar si falta un CTA
    admin = make_pool(s.database_url_admin, "admin", max_size=5)
    support = make_pool(s.database_url_support_ro, "support_ro")
    sales = make_pool(s.database_url_sales_ro, "sales_ro")
    checkpointer = PostgresSaver(admin)
    checkpointer.setup()
    services = Services(
        generator_llm=make_chat_model(s.llm_generator_model),
        grader_llm=make_chat_model(s.llm_grader_model),
        support_store=PgChunkStore("support", support, s.top_k, s.candidate_k),
        sales_store=PgChunkStore("sales", sales, s.top_k, s.candidate_k),
        embedder=GeminiEmbedder(s.google_api_key, s.embedding_model, s.embedding_dim),
        config=cfg, min_vector_score=s.min_vector_score, max_ret=s.max_ret, max_gen=s.max_gen,
    )
    app.state.graph = build_graph(services, checkpointer)
    app.state.admin_pool = admin
    yield
    for p in (admin, support, sales):
        p.close()


app = FastAPI(title="ECO-KB", lifespan=lifespan)


@app.get("/health")
def health():
    try:
        with app.state.admin_pool.connection() as conn:
            conn.execute("SELECT 1")
    except Exception as exc:
        raise HTTPException(503, f"db: {exc}") from exc
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    config = {"configurable": {"thread_id": req.session_id}, "recursion_limit": 25}
    state = app.state.graph.invoke(req.model_dump(), config)
    audit = state.get("audit_log", [])
    start = max((i for i, e in enumerate(audit) if e["event"] == "turn_start"), default=0)
    return ChatResponse(
        answer=state["final_answer"], flow=state["flow"],
        intent="safety" if state.get("fallback_reason") == "safety" else state.get("intent", "business_question"), sources=state.get("sources", []),
        fallback_reason=state.get("fallback_reason"), cta_url=state.get("cta_url"),
        audit_log=audit[start:],
    )
