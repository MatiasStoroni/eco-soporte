from urllib.parse import parse_qs, urlsplit

from eco_kb.graph.nodes.finalize import make_finalize_sales, strip_urls
from tests.conftest import FakeLLM, chunk


def _run(make_services, state):
    node = make_finalize_sales(make_services(FakeLLM({})))
    return node({"client_type": "bodega", "relevant_docs": [chunk("a#0", "x")], **state})


def test_cta_on_success_and_strips_llm_urls(make_services):
    out = _run(make_services, {"answer": "Mira https://evil.example/x y www.otro.com ok.",
                               "cited_chunk_ids": ["a#0"], "fallback_reason": None})
    assert "evil.example" not in out["final_answer"] and "otro.com" not in out["final_answer"]
    assert out["cta_url"] and out["final_answer"].endswith(out["cta_url"])
    q = parse_qs(urlsplit(out["cta_url"]).query)
    assert q["utm_campaign"] == ["bodega"]


def test_cta_on_fallback(make_services):
    out = _run(make_services, {"answer": "", "fallback_reason": "no_documents"})
    assert out["cta_url"] and "equipo comercial" in out["final_answer"]


def test_strip_urls():
    assert strip_urls("a http://x.com b") == "a b"
