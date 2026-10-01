from eco_kb.graph.nodes.retrieval_nodes import format_catalog, fuse_chunk_lists


def c(cid, score):
    return {"chunk_id": cid, "vec_score": score, "rrf": 0.0}


def test_fuse_prefers_chunks_found_by_several_queries_and_keeps_best_score():
    fused = fuse_chunk_lists([[c("a", 0.5), c("b", 0.9)], [c("b", 0.6), c("c", 0.7)], [c("b", None)]])
    assert [x["chunk_id"] for x in fused][0] == "b"
    assert next(x for x in fused if x["chunk_id"] == "b")["vec_score"] == 0.9
    assert len(fused) == 3


def test_fuse_top_n_and_none_scores():
    fused = fuse_chunk_lists([[c(str(i), None) for i in range(20)]], top_n=5)
    assert len(fused) == 5 and fused[0]["vec_score"] is None


def test_format_catalog():
    txt = format_catalog([{"title": "Manual", "product": "ECO360", "sections": ["A", "B"]}])
    assert txt == "- Manual · ECO360 · A; B"
