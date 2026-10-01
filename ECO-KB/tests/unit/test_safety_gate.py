import pytest

from eco_kb.graph.nodes.safety import is_safety_query


@pytest.mark.parametrize("q", [
    "¿Puedo mezclar el producto con lejía?",
    "Me ha salpicado en los ojos",
    "¿Es seguro inhalar vapores?",
    "he mezclado dos productos químicos",
    "puedo mezclar x4 con cloro",
])
def test_flagged(app_config, q):
    assert is_safety_query(q, app_config.safety)


@pytest.mark.parametrize("q", ["¿Qué significa el código E04?", "Quiero información de la gama", "x4 tiene cloro?"])
def test_not_flagged(app_config, q):
    assert not is_safety_query(q, app_config.safety)
