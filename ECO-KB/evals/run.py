"""Evalúa el grafo real (LLM + embeddings + Postgres) con evals/questions.yaml.

    uv run python -m evals.run [--runs 2] [--only texto]
Consume cuota de la API de Gemini. No es parte de pytest.
"""
import argparse
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

from eco_kb.config import load_config
from eco_kb.db import make_pool
from eco_kb.graph.builder import build_graph
from eco_kb.graph.services import Services
from eco_kb.llm import GeminiEmbedder, make_chat_model
from eco_kb.retrieval.store import PgChunkStore
from eco_kb.settings import get_settings


def build():
    s = get_settings()
    cfg = load_config(s.clients_path, s.safety_path, s.handoff_path)
    sup = make_pool(s.database_url_support_ro, "ev_sup")
    sal = make_pool(s.database_url_sales_ro, "ev_sal")
    services = Services(
        generator_llm=make_chat_model(s.llm_generator_model, s.llm_generator_thinking),
        grader_llm=make_chat_model(s.llm_grader_model, s.llm_grader_thinking),
        support_store=PgChunkStore("support", sup, s.top_k, s.candidate_k),
        sales_store=PgChunkStore("sales", sal, s.top_k, s.candidate_k),
        embedder=GeminiEmbedder(s.google_api_key, s.embedding_model, s.embedding_dim),
        config=cfg, min_vector_score=s.min_vector_score, max_ret=s.max_ret, max_gen=s.max_gen,
    )
    return build_graph(services)


def check(case: dict, out: dict) -> tuple[bool, str]:
    sections = [x["section"] for x in out.get("sources", [])]
    wanted = case.get("intent") if isinstance(case.get("intent"), list) else [case.get("intent")]
    if "intent" in case and out.get("intent") not in wanted:
        return False, f"intent={out.get('intent')} (esperado {case['intent']})"
    chat_only = "intent" in case and "expect" not in case and "business_question" not in wanted
    if "intent" in case and not chat_only and "expect" not in case:
        return (out.get("fallback_reason") != "off_topic", "una pregunta de negocio no debe tratarse como charla")
    answer = out.get("answer", "").lower()
    # contains: alguna subcadena debe estar en la respuesta; absent: ninguna puede estar.
    if case.get("contains") and not any(x.lower() in answer for x in case["contains"]):
        return False, f"la respuesta no contiene ninguno de {case['contains']}"
    if hit := [x for x in case.get("absent", []) if x.lower() in answer]:
        return False, f"la respuesta contiene {hit}"
    if chat_only:  # conversación y respuestas fijas: sin fuentes; la única URL posible es el CTA del código
        if out.get("sources") or ("http" in answer and not out.get("cta_url")):
            return False, "la charla no debe citar fuentes ni URLs"
        return True, ""
    if case.get("fallback") == "maybe":  # negarse o decir que el dato no está cargado: ambas valen
        return True, ""
    if case.get("fallback"):
        return (out.get("fallback_reason") is not None, "debía negarse y respondió")
    if out.get("fallback_reason"):
        return False, f"fallback={out['fallback_reason']}"
    if not any(e.lower() in sec.lower() for e in case["expect"] for sec in sections):
        return False, f"fuentes inesperadas: {sections}"
    return True, ""


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--only", default="")
    ap.add_argument("--flow", choices=["support", "sales"], help="solo ese flujo (incluye la charla en ese flujo)")
    ap.add_argument("--show", action="store_true", help="imprime las respuestas de conversación")
    args = ap.parse_args()
    data = yaml.safe_load(Path(__file__).with_name("questions.yaml").read_text(encoding="utf-8"))
    graph = build()

    jobs = []
    for flow, cases in data.items():
        for c in cases:
            if args.only in c["q"]:
                jobs += [("support", c), ("sales", c)] if flow == "chat" else [(flow, c)]
    jobs = [j for j in jobs if not args.flow or j[0] == args.flow] * args.runs

    def run(job):
        flow, c = job
        out = graph.invoke(
            {"session_id": uuid.uuid4().hex, "is_registered": flow == "support",
             "client_type": c.get("client_type", "hotel"), "language": "es", "message": c["q"]},
            {"recursion_limit": 25})
        return job, out

    with ThreadPoolExecutor(4) as ex:
        results = list(ex.map(run, jobs))

    fails, ok = [], 0
    for (flow, c), out in results:
        good, why = check(c, {"fallback_reason": out.get("fallback_reason"), "sources": out.get("sources", []),
                                "intent": out.get("intent"), "answer": out.get("final_answer", ""),
                                "cta_url": out.get("cta_url")})
        ok += good
        if not good:
            q = next((f'{e["intent"]} | {e["queries"]}' for e in out["audit_log"] if e["event"] == "rewrite_query"), "?")
            show_answer = "intent" in c or "contains" in c or "absent" in c
            fails.append((flow, c["q"], why, out.get("final_answer", "")[:300] if show_answer else q))
    if args.show:
        for (flow, c), out in results:
            if "intent" in c and "expect" not in c:
                print(f"[{flow}] {c['q']!r} -> {out.get('intent')}: {out.get('final_answer', '')[:400]}")
    print(f"\nACIERTOS: {ok}/{len(results)} ({100 * ok // max(len(results), 1)}%)")
    for flow, q, why, rq in fails:
        print(f"  ✗ [{flow}] {q!r}\n      {why}\n      rewrite: {rq!r}")


if __name__ == "__main__":
    main()
