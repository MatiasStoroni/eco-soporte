from fastapi.testclient import TestClient

from eco_kb.settings import get_settings

from eco_kb.api.main import app
from eco_kb.graph.builder import build_graph
from tests.conftest import FakeLLM
from tests.e2e.test_graph_fakes import SAL, script


def test_chat_endpoint(make_services):
    llm = FakeLLM(script(SAL["chunk_id"], "EcoTank 20 limpia depósitos de acero inoxidable."))
    app.state.graph = build_graph(make_services(llm, sales_chunks=[SAL]))
    client = TestClient(app)  # sin 'with': no ejecuta el lifespan (BD/LLM reales)
    body = {"session_id": "s1", "is_registered": False, "client_type": "bodega", "message": "¿Depósitos?"}
    r = client.post("/chat", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["flow"] == "sales" and data["cta_url"] and data["audit_log"][0]["event"] == "turn_start"

    assert client.post("/chat", json={**body, "client_type": "banco"}).status_code == 422


def test_admin_requires_token(monkeypatch):
    client = TestClient(app)
    monkeypatch.setattr(get_settings(), "admin_token", "")
    assert client.get("/admin/ping").status_code == 503  # sin token: panel desactivado
    monkeypatch.setattr(get_settings(), "admin_token", "secreto")
    assert client.get("/admin/ping").status_code == 401
    assert client.get("/admin/ping", headers={"Authorization": "Bearer otro"}).status_code == 401
    assert client.get("/admin/ping", headers={"Authorization": "Bearer secreto"}).status_code == 200
