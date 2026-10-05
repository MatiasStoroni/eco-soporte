import pytest

from eco_kb.graph.builder import build_graph
from eco_kb.graph.nodes.common import ProtectedFieldError, guarded
from eco_kb.graph.schemas import (AnswerCheckOut, ChatReplyOut, ChunkGrade, GenerationOut, GradeOut,
                                  GroundingOut, IntentOut, RewriteOut)
from tests.conftest import FakeLLM, chunk

SUP = chunk("s/hotel/a.md#0", "Diluya EC-100 al 2% (20 ml por litro).", "support", ("hotel",))
SAL = chunk("s/bodega/b.md#0", "EcoTank 20 limpia depósitos de acero inoxidable.", "sales", ("bodega",))


def payload(**kw):
    base = {"session_id": "t1", "is_registered": True, "client_type": "hotel", "language": "es",
            "message": "¿Cómo diluyo EC-100?"}
    return {**base, **kw}


def script(cid, answer, grounded=True, answers=True, relevant=True):
    return {
        IntentOut: [IntentOut(intent="business_question")],
        RewriteOut: [RewriteOut(intent="dilución de EC-100", queries=["dilución EC-100", "EC-100 2%"])],
        GradeOut: [GradeOut(grades=[ChunkGrade(chunk_id=cid, relevant=relevant)])],
        GenerationOut: [GenerationOut(answer=answer, cited_chunk_ids=[cid])],
        GroundingOut: [GroundingOut(grounded=grounded)],
        AnswerCheckOut: [AnswerCheckOut(answers_question=answers, reason="x")],
    }


def run(services, **kw):
    return build_graph(services).invoke(payload(**kw), {"recursion_limit": 25})


def test_support_ok(make_services):
    llm = FakeLLM(script(SUP["chunk_id"], "Diluya EC-100 al 2%."))
    out = run(make_services(llm, support_chunks=[SUP]))
    assert out["flow"] == "support" and out["fallback_reason"] is None
    assert out["final_answer"] == "Diluya EC-100 al 2%." and out["cta_url"] is None
    assert out["sources"] == [{"title": "Doc", "section": "Sec"}]


def test_sales_ok_has_cta_and_uses_only_sales_store(make_services):
    llm = FakeLLM(script(SAL["chunk_id"], "Visita https://x.com. EcoTank 20 limpia depósitos."))
    svc = make_services(llm, support_chunks=[SUP], sales_chunks=[SAL])
    out = run(svc, is_registered=False, client_type="bodega", message="¿Qué ofrecen para depósitos?")
    assert out["flow"] == "sales" and out["cta_url"] and out["final_answer"].endswith(out["cta_url"])
    assert "x.com" not in out["final_answer"]
    assert svc.support_store.filters == [] and svc.sales_store.filters[0].audience == "sales"
    assert svc.sales_store.filters[0].allowed_client_types == ("bodega", "common")


def test_no_documents_sales_fallback_with_cta(make_services):
    llm = FakeLLM(script(SAL["chunk_id"], "x", relevant=False))
    out = run(make_services(llm, sales_chunks=[SAL]), is_registered=False, client_type="bodega")
    assert out["fallback_reason"] == "no_documents" and out["cta_url"]
    assert llm.count("RewriteOut") == 2  # MAX_RET=2 -> un reintento


def test_support_no_documents_fallback(make_services):
    llm = FakeLLM(script(SUP["chunk_id"], "x", relevant=False))
    out = run(make_services(llm, support_chunks=[SUP]))
    assert out["fallback_reason"] == "no_documents" and out["cta_url"] is None
    assert "documentación técnica" in out["final_answer"]


def test_hallucination_retry_then_fallback(make_services):
    llm = FakeLLM(script(SUP["chunk_id"], "Diluya EC-100 al 9%."))  # 9% inventado -> regla regex
    out = run(make_services(llm, support_chunks=[SUP]))
    assert out["fallback_reason"] == "ungrounded"
    assert llm.count("GenerationOut") == 3 and llm.count("GroundingOut") == 0  # MAX_GEN=3


def test_llm_grounding_failure_then_recovers(make_services):
    s = script(SUP["chunk_id"], "Diluya EC-100 al 2%.")
    s[GroundingOut] = [GroundingOut(grounded=False, unsupported_claims=["x"]), GroundingOut(grounded=True)]
    llm = FakeLLM(s)
    out = run(make_services(llm, support_chunks=[SUP]))
    assert out["fallback_reason"] is None and llm.count("GenerationOut") == 2


def test_answer_check_failure(make_services):
    llm = FakeLLM(script(SUP["chunk_id"], "Diluya EC-100 al 2%.", answers=False))
    out = run(make_services(llm, support_chunks=[SUP]))
    assert out["fallback_reason"] == "answer_mismatch"


def test_safety_question_never_calls_llm(make_services):
    llm = FakeLLM({})
    out = run(make_services(llm), message="¿Puedo mezclar el producto con lejía?")
    assert out["fallback_reason"] == "safety" and "SDS" in out["final_answer"] and llm.calls == []


def test_cross_tenant_chunk_dropped(make_services):
    bad = chunk("s/bodega/z.md#0", "secreto", "support", ("bodega",))
    llm = FakeLLM(script(SUP["chunk_id"], "Diluya EC-100 al 2%."))
    out = run(make_services(llm, support_chunks=[bad, SUP]))
    ids = [e for e in out["audit_log"] if e["event"] == "retrieve"][0]["chunk_ids"]
    assert ids == [SUP["chunk_id"]]
    assert any(e["event"] == "security_event" for e in out["audit_log"])


def test_protected_fields_guard():
    with pytest.raises(ProtectedFieldError):
        guarded("evil", lambda s: {"flow": "support"})({})


def test_invalid_client_type_rejected(make_services):
    with pytest.raises(ValueError):
        run(make_services(FakeLLM({})), client_type="banco")


def test_rewrite_gets_domain_context_and_retrieval_uses_all_queries(make_services):
    llm = FakeLLM(script(SUP["chunk_id"], "Diluya EC-100 al 2%."))
    svc = make_services(llm, support_chunks=[SUP])
    seen = []
    orig = svc.support_store.search
    svc.support_store.search = lambda v, q, f: (seen.append(q), orig(v, q, f))[1]
    out = run(svc, message="q diluciom pa el ec100??")
    system = next(m for n, m in llm.calls if n == "RewriteOut")[0][1]
    assert "Glosario" in system and "Doc · P · Sec" in system  # glosario + catálogo en el prompt
    assert seen == ["dilución EC-100", "EC-100 2%", "q diluciom pa el ec100??"]  # + mensaje original
    assert out["fallback_reason"] is None


# ---------- intenciones de conversación ----------

def chat_llm(intent, reply="¡Hola! Te ayudo con productos ECO360. ¿Qué necesitas?"):
    return FakeLLM({IntentOut: [IntentOut(intent=intent)], ChatReplyOut: [ChatReplyOut(reply=reply)]})


@pytest.mark.parametrize("intent,msg", [
    ("greeting", "hola"), ("smalltalk", "gracias!"), ("capabilities", "q sabes hacer?"),
    ("unclear", "?"), ("off_topic", "contame un chiste"),
])
def test_chitchat_never_touches_rag(make_services, intent, msg):
    llm = chat_llm(intent)
    svc = make_services(llm, support_chunks=[SUP], sales_chunks=[SAL])
    out = run(svc, message=msg)
    assert out["intent"] == intent and out["final_answer"].startswith("¡Hola!")
    assert out["sources"] == [] and out["cta_url"] is None
    assert svc.support_store.filters == [] and llm.count("RewriteOut") == 0  # sin búsqueda ni generación
    assert out["fallback_reason"] == ("off_topic" if intent == "off_topic" else None)
    assert out["messages"][-1].content == out["final_answer"]


def test_chitchat_in_sales_flow_and_urls_stripped(make_services):
    llm = chat_llm("greeting", "Hola, visita https://x.com o www.otra.com. ¿Qué te interesa?")
    out = run(make_services(llm), is_registered=False, client_type="bodega", message="buenas")
    assert out["flow"] == "sales" and "x.com" not in out["final_answer"] and "otra.com" not in out["final_answer"]
    assert out["cta_url"] is None
    system = next(m for n, m in llm.calls if n == "ChatReplyOut")[0][1]
    assert "beneficios" in system  # capacidades del flujo de ventas, no las de soporte


def test_business_question_goes_through_rag(make_services):
    llm = FakeLLM(script(SUP["chunk_id"], "Diluya EC-100 al 2%."))
    out = run(make_services(llm, support_chunks=[SUP]))
    assert out["intent"] == "business_question" and llm.count("IntentOut") == 1
    assert llm.count("ChatReplyOut") == 0


def test_classifier_failure_defaults_to_business(make_services):
    s = script(SUP["chunk_id"], "Diluya EC-100 al 2%.")
    s[IntentOut] = [lambda m: (_ for _ in ()).throw(RuntimeError("llm caído"))]
    out = run(make_services(FakeLLM(s), support_chunks=[SUP]))
    assert out["intent"] == "business_question" and out["fallback_reason"] is None


def test_converse_failure_uses_canned_reply(make_services):
    llm = FakeLLM({IntentOut: [IntentOut(intent="off_topic")],
                   ChatReplyOut: [lambda m: (_ for _ in ()).throw(RuntimeError("llm caído"))]})
    out = run(make_services(llm), message="quién ganó el mundial")
    assert out["final_answer"].startswith("Solo puedo ayudarte con temas de nuestro negocio")
    assert "ECO360" in out["final_answer"]  # redirige con las capacidades de la config


def test_safety_takes_priority_over_intent(make_services):
    llm = chat_llm("greeting")
    out = run(make_services(llm), message="hola, puedo mezclar esto con lejía?")
    assert out["fallback_reason"] == "safety" and llm.calls == []


def test_handoff_request_is_deterministic_and_skips_llm(make_services):
    llm = FakeLLM(script(SUP["chunk_id"], "x"))
    out = run(make_services(llm, support_chunks=[SUP]), message="quiero hablar con una persona")
    assert out["handoff_requested"] and out["intent"] == "handoff" and llm.calls == []
    assert out["final_answer"] and out["sources"] == [] and out["cta_url"] is None


def test_safety_goes_before_handoff(make_services):
    llm = FakeLLM(script(SUP["chunk_id"], "x"))
    out = run(make_services(llm, support_chunks=[SUP]), message="me salpicó en los ojos, pasame con una persona")
    assert out["fallback_reason"] == "safety" and not out["handoff_requested"]


# ---------- respuestas fijas hacia el equipo comercial ----------

@pytest.mark.parametrize("registered", [True, False])
def test_purchase_skips_rag_requests_handoff_and_adds_cta(make_services, registered):
    llm = FakeLLM({IntentOut: [IntentOut(intent="purchase")]})
    svc = make_services(llm, support_chunks=[SUP], sales_chunks=[SAL])
    out = run(svc, is_registered=registered, message="quiero comprar más x5")
    assert out["intent"] == "purchase" and out["handoff_requested"] and out["fallback_reason"] is None
    assert out["cta_url"] and out["final_answer"].endswith(out["cta_url"]) and out["sources"] == []
    assert svc.support_store.filters == [] and svc.sales_store.filters == [] and [n for n, _ in llm.calls if n != "IntentOut"] == []


def test_technical_question_in_sales_is_redirected_without_rag(make_services):
    llm = FakeLLM({IntentOut: [IntentOut(intent="technical_question")]})
    svc = make_services(llm, sales_chunks=[SAL])
    out = run(svc, is_registered=False, client_type="bodega", message="como limpio con ozono?")
    assert out["fallback_reason"] == "technical" and out["sources"] == []
    assert out["cta_url"] and out["final_answer"].endswith(out["cta_url"]) and "bodega" in out["final_answer"]
    assert svc.sales_store.filters == [] and llm.count("RewriteOut") == 0


def test_technical_question_in_support_goes_through_rag(make_services):
    s = script(SUP["chunk_id"], "Diluya EC-100 al 2%.")
    s[IntentOut] = [IntentOut(intent="technical_question")]
    out = run(make_services(FakeLLM(s), support_chunks=[SUP]))
    assert out["fallback_reason"] is None and out["final_answer"] == "Diluya EC-100 al 2%."


def test_capabilities_are_per_client_type(make_services):
    llm = chat_llm("capabilities")
    run(make_services(llm), client_type="bodega", message="q sabes hacer?")
    system = next(m for n, m in llm.calls if n == "ChatReplyOut")[0][1]
    assert "Carro Ozonify Industrial" in system and "paño" not in system  # bodega no ve el manual de hotel


# ---------- fallos del LLM dentro del RAG: nunca tumban el turno ----------

def _boom(m):
    raise ValueError("salida estructurada vacía")


def test_generate_failure_is_retried(make_services):
    s = script(SUP["chunk_id"], "Diluya EC-100 al 2%.")
    s[GenerationOut] = [_boom, GenerationOut(answer="Diluya EC-100 al 2%.", cited_chunk_ids=[SUP["chunk_id"]])]
    out = run(make_services(FakeLLM(s), support_chunks=[SUP]))
    assert out["fallback_reason"] is None and out["final_answer"] == "Diluya EC-100 al 2%."
    assert [e.get("error") for e in out["audit_log"] if e["event"] == "generate"] == ["ValueError", None]


def test_generate_always_failing_ends_in_fallback(make_services):
    s = script(SUP["chunk_id"], "x")
    s[GenerationOut] = [_boom]
    out = run(make_services(FakeLLM(s), support_chunks=[SUP]))
    assert out["fallback_reason"] == "ungrounded" and out["final_answer"]


def test_grader_failures_degrade_gracefully(make_services):
    s = script(SUP["chunk_id"], "Diluya EC-100 al 2%.")
    s[RewriteOut], s[GradeOut], s[AnswerCheckOut] = [_boom], [_boom], [_boom]
    out = run(make_services(FakeLLM(s), support_chunks=[SUP]))
    assert out["fallback_reason"] is None and out["final_answer"] == "Diluya EC-100 al 2%."
