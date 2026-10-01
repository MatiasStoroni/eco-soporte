"""Funciones de enrutamiento puras (sin LLM ni I/O)."""


def route_by_registration(state: dict) -> str:
    return "support" if state["is_registered"] is True else "sales"


def after_safety(state: dict) -> str:
    return "unsafe" if state.get("safety_flag") else "safe"


def after_intent(state: dict) -> str:
    """Solo las preguntas de negocio llegan al RAG; el resto lo atiende el nodo conversacional."""
    return "business" if state.get("intent", "business_question") == "business_question" else "chitchat"


def after_grading(state: dict, *, max_ret: int) -> str:
    if state.get("relevant_docs"):
        return "generate"
    return "rewrite" if state.get("attempts_ret", 0) < max_ret else "no_answer"


def after_grounding(state: dict, *, max_gen: int) -> str:
    if state.get("grounding_ok"):
        return "answer_check"
    return "generate" if state.get("attempts_gen", 0) < max_gen else "no_answer"


def after_answer_check(state: dict, *, max_gen: int) -> str:
    if state.get("answer_ok"):
        return "finalize"
    return "generate" if state.get("attempts_gen", 0) < max_gen else "no_answer"
