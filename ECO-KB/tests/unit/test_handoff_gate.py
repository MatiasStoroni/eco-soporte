from pathlib import Path

import pytest
import yaml

from eco_kb.config import HandoffConfig
from eco_kb.graph.nodes.handoff import is_handoff_request


@pytest.mark.parametrize("q", [
    "quiero hablar con una persona",
    "Puedo hablar con un humano?",
    "pasame con un asesor",
    "me podés comunicar con alguien del equipo?",
    "necesito contactar a un vendedor",
    "quiero atención humana",
    "me pasás con un asesor?",
    "pásame con alguien del equipo",
    "quiero que me atienda una persona",
])
def test_flagged(app_config, q):
    assert is_handoff_request(q, app_config.handoff)


@pytest.mark.parametrize("q", [
    "¿Cuántas personas hacen falta para usar el carro?",
    "q beneficios tiene para los huéspedes",
    "hola",
    "¿qué sabés hacer?",
    "puedo pasar el carro donde hay personas?",
    "el carro lo atiende una sola persona?",
])
def test_not_flagged(app_config, q):
    assert not is_handoff_request(q, app_config.handoff)


def test_eval_questions_never_trigger_handoff(app_config):
    """La derivación es último recurso: ninguna pregunta real de la batería debe dispararla."""
    cases = yaml.safe_load(Path("evals/questions.yaml").read_text(encoding="utf-8"))
    hits = [c["q"] for flow in cases.values() for c in flow if is_handoff_request(c["q"], app_config.handoff)]
    assert hits == []


def test_disabled_never_triggers():
    assert not is_handoff_request("quiero hablar con una persona", HandoffConfig(keywords=["persona"]))
