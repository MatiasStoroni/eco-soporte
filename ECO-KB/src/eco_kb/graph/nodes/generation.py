"""generate, check_grounding, check_answer."""
import re

from eco_kb.graph import prompts
from eco_kb.graph.nodes.common import audit, history_text
from eco_kb.graph.schemas import AnswerCheckOut, GenerationOut, GroundingOut
from eco_kb.graph.services import Services

_UNITS = (r"(?:%|ml|cl|l|litros?|g|gr|kg|mg|ppm|°c|ºc|°|min|minutos?|h|horas?|seg|segundos?|"
          r"mm|cm|m|bar|ph|días|dias|meses)")
_NUM_UNIT = re.compile(rf"\d+(?:\.\d+)?{_UNITS}(?![a-záéíóúñ])")
_RATIO = re.compile(r"\d+(?:\.\d+)?[:/]\d+(?:\.\d+)?")
_CODE = re.compile(r"\b[a-z]{1,3}-?\d{2,4}\b")


def _normalize(text: str) -> str:
    t = text.lower()
    t = re.sub(r"(\d),(\d)", r"\1.\2", t)
    t = re.sub(rf"(\d)\s+({_UNITS})(?![a-záéíóúñ])", r"\1\2", t)
    t = re.sub(r"(\d)\s*([:/])\s*(\d)", r"\1\2\3", t)
    return t


def check_numeric_claims(answer: str, cited_texts: list[str]) -> list[str]:
    """Cifras con unidad, ratios y códigos de la respuesta que NO aparecen literalmente en los chunks citados."""
    context = _normalize("\n".join(cited_texts))
    ans = _normalize(answer)
    tokens = set(_NUM_UNIT.findall(ans)) | set(_RATIO.findall(ans)) | set(_CODE.findall(ans))
    return sorted(t for t in tokens if t not in context)


def make_generate(services: Services, flow: str):
    llm = services.generator_llm.with_structured_output(GenerationOut)
    dom = services.config.domain
    domain = prompts.domain_context(dom.description, dom.glossary)

    def generate(state: dict) -> dict:
        ct = state["client_type"]
        system = prompts.generate_system(
            flow, ct, services.config.clients[ct].tone, state.get("language", "es"), domain)
        context = "\n\n".join(f"[{d['chunk_id']}]\n{d['content']}" for d in state["relevant_docs"])
        user = (f"Historial:\n{history_text(state['messages'])}\n\nFRAGMENTOS:\n{context}\n\n"
                f"PREGUNTA: {state['message']}")
        if state.get("feedback"):
            user += f"\n\nCORRIGE el intento anterior: {state['feedback']}"
        out: GenerationOut = llm.invoke([("system", system), ("human", user)])
        return {
            "answer": out.answer, "cited_chunk_ids": out.cited_chunk_ids,
            "attempts_gen": state.get("attempts_gen", 0) + 1,
            "audit_log": audit("generate", attempt=state.get("attempts_gen", 0) + 1,
                               cited=out.cited_chunk_ids),
        }

    return generate


def make_check_grounding(services: Services):
    llm = services.grader_llm.with_structured_output(GroundingOut)

    def check_grounding(state: dict) -> dict:
        by_id = {d["chunk_id"]: d for d in state["relevant_docs"]}
        cited = state.get("cited_chunk_ids", [])

        def fail(reason: str, feedback: str) -> dict:
            return {"grounding_ok": False, "feedback": feedback,
                    "audit_log": audit("check_grounding", ok=False, reason=reason)}

        # 1) citas ⊂ docs
        if not cited:
            return fail("no_citations", "La respuesta debe citar al menos un chunk_id.")
        unknown = [c for c in cited if c not in by_id]
        if unknown:
            return fail("unknown_citations", f"Citaste chunk_id inexistentes: {unknown}.")
        texts = [by_id[c]["content"] for c in cited]
        # 2) reglas regex
        missing = check_numeric_claims(state["answer"], texts)
        if missing:
            return fail("numeric_mismatch", f"Estas cifras/códigos no aparecen en los fragmentos: {missing}. Elimínalos.")
        # 3) LLM grader, solo después de las reglas
        out: GroundingOut = llm.invoke([
            ("system", prompts.GROUNDING),
            ("human", "FRAGMENTOS:\n" + "\n\n".join(texts) + f"\n\nRESPUESTA:\n{state['answer']}"),
        ])
        if not out.grounded:
            return fail("llm_ungrounded", f"Afirmaciones sin respaldo: {out.unsupported_claims}. Elimínalas.")
        return {"grounding_ok": True, "feedback": "", "audit_log": audit("check_grounding", ok=True)}

    return check_grounding


def make_check_answer(services: Services):
    llm = services.grader_llm.with_structured_output(AnswerCheckOut)
    dom = services.config.domain
    domain = prompts.domain_context(dom.description, dom.glossary)

    def check_answer(state: dict) -> dict:
        out: AnswerCheckOut = llm.invoke([
            ("system", prompts.ANSWER_CHECK.format(domain=domain)),
            ("human", f"PREGUNTA DEL USUARIO (informal): {state['message']}\n"
                      f"PREGUNTA INTERPRETADA: {state['query']}\n\nRESPUESTA:\n{state['answer']}"),
        ])
        fb = "" if out.answers_question else f"No responde a la pregunta: {out.reason}"
        return {"answer_ok": out.answers_question, "feedback": fb,
                "audit_log": audit("check_answer", ok=out.answers_question, reason=out.reason)}

    return check_answer
