from eco_kb.graph import edges


def test_route_by_registration():
    assert edges.route_by_registration({"is_registered": True}) == "support"
    assert edges.route_by_registration({"is_registered": False}) == "sales"


def test_after_safety():
    assert edges.after_safety({"safety_flag": True}) == "unsafe"
    assert edges.after_safety({"safety_flag": False}) == "safe"


def test_after_grading():
    assert edges.after_grading({"relevant_docs": [{}], "attempts_ret": 5}, max_ret=2) == "generate"
    assert edges.after_grading({"relevant_docs": [], "attempts_ret": 1}, max_ret=2) == "rewrite"
    assert edges.after_grading({"relevant_docs": [], "attempts_ret": 2}, max_ret=2) == "no_answer"


def test_after_grounding():
    assert edges.after_grounding({"grounding_ok": True}, max_gen=2) == "answer_check"
    assert edges.after_grounding({"grounding_ok": False, "attempts_gen": 1}, max_gen=2) == "generate"
    assert edges.after_grounding({"grounding_ok": False, "attempts_gen": 2}, max_gen=2) == "no_answer"


def test_after_answer_check():
    assert edges.after_answer_check({"answer_ok": True}, max_gen=2) == "finalize"
    assert edges.after_answer_check({"answer_ok": False, "attempts_gen": 1}, max_gen=2) == "generate"
    assert edges.after_answer_check({"answer_ok": False, "attempts_gen": 2}, max_gen=2) == "no_answer"


def test_after_intent():
    assert edges.after_intent({"intent": "business_question"}) == "business"
    assert edges.after_intent({}) == "business"
    for i in ("greeting", "smalltalk", "capabilities", "off_topic", "unclear"):
        assert edges.after_intent({"intent": i}) == "chitchat"
