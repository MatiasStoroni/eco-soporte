from langgraph.graph import END, START, StateGraph

from eco_kb.graph import edges
from eco_kb.graph.nodes.common import guarded
from eco_kb.graph.nodes.finalize import make_finalize_sales, make_finalize_support, make_no_answer
from eco_kb.graph.nodes.generation import make_check_answer, make_check_grounding, make_generate
from eco_kb.graph.nodes.handoff import make_handoff_gate, make_handoff_response
from eco_kb.graph.nodes.intent import make_classify_intent, make_converse
from eco_kb.graph.nodes.redirects import make_purchase_response, make_sales_technical_response
from eco_kb.graph.nodes.retrieval_nodes import make_grade_documents, make_retrieve, make_rewrite_query
from eco_kb.graph.nodes.safety import make_safety_gate, make_safety_response
from eco_kb.graph.nodes.session import make_validate_session
from eco_kb.graph.services import Services
from eco_kb.graph.state import GraphInput, GraphState


def build_rag_subgraph(flow: str, store, services: Services):
    """Subgrafo Self-RAG de un flujo. Solo conoce SU store."""
    g = StateGraph(GraphState)
    finalize = make_finalize_support(services) if flow == "support" else make_finalize_sales(services)
    nodes = {
        "rewrite_query": make_rewrite_query(services, store),
        "retrieve": make_retrieve(services, store),
        "grade_documents": make_grade_documents(services),
        "generate": make_generate(services, flow),
        "check_grounding": make_check_grounding(services),
        "check_answer": make_check_answer(services),
        "no_answer": make_no_answer(),
        "finalize": finalize,
    }
    for name, fn in nodes.items():
        g.add_node(name, guarded(name, fn))

    g.add_edge(START, "rewrite_query")
    g.add_edge("rewrite_query", "retrieve")
    g.add_edge("retrieve", "grade_documents")
    g.add_conditional_edges(
        "grade_documents", lambda s: edges.after_grading(s, max_ret=services.max_ret),
        {"generate": "generate", "rewrite": "rewrite_query", "no_answer": "no_answer"},
    )
    g.add_edge("generate", "check_grounding")
    g.add_conditional_edges(
        "check_grounding", lambda s: edges.after_grounding(s, max_gen=services.max_gen),
        {"answer_check": "check_answer", "generate": "generate", "no_answer": "no_answer"},
    )
    g.add_conditional_edges(
        "check_answer", lambda s: edges.after_answer_check(s, max_gen=services.max_gen),
        {"finalize": "finalize", "generate": "generate", "no_answer": "no_answer"},
    )
    g.add_edge("no_answer", "finalize")
    g.add_edge("finalize", END)  # única salida a END de la rama (en ventas: finalize_sales)
    return g.compile()


def build_graph(services: Services, checkpointer=None):
    g = StateGraph(GraphState, input_schema=GraphInput)
    g.add_node("validate_session", make_validate_session(services))  # único que fija campos protegidos
    g.add_node("safety_gate", guarded("safety_gate", make_safety_gate(services)))
    g.add_node("safety_response", guarded("safety_response", make_safety_response(services)))
    g.add_node("handoff_gate", guarded("handoff_gate", make_handoff_gate(services)))
    g.add_node("handoff_response", guarded("handoff_response", make_handoff_response(services)))
    g.add_node("classify_intent", guarded("classify_intent", make_classify_intent(services)))
    g.add_node("converse", guarded("converse", make_converse(services)))
    g.add_node("purchase_response", guarded("purchase_response", make_purchase_response(services)))
    g.add_node("sales_technical_response",
               guarded("sales_technical_response", make_sales_technical_response(services)))
    g.add_node("router", lambda state: {})
    g.add_node("support_rag", build_rag_subgraph("support", services.support_store, services))
    g.add_node("sales_rag", build_rag_subgraph("sales", services.sales_store, services))

    g.add_edge(START, "validate_session")
    g.add_edge("validate_session", "safety_gate")
    g.add_conditional_edges("safety_gate", edges.after_safety,
                            {"unsafe": "safety_response", "safe": "handoff_gate"})
    g.add_conditional_edges("handoff_gate", edges.after_handoff,
                            {"handoff": "handoff_response", "continue": "classify_intent"})
    g.add_conditional_edges("classify_intent", edges.after_intent,
                            {"business": "router", "chitchat": "converse", "purchase": "purchase_response",
                             "sales_technical": "sales_technical_response"})
    g.add_edge("converse", END)
    g.add_edge("purchase_response", END)
    g.add_edge("sales_technical_response", END)
    g.add_conditional_edges("router", edges.route_by_registration,
                            {"support": "support_rag", "sales": "sales_rag"})
    g.add_edge("safety_response", END)
    g.add_edge("handoff_response", END)
    g.add_edge("support_rag", END)
    g.add_edge("sales_rag", END)
    return g.compile(checkpointer=checkpointer)
