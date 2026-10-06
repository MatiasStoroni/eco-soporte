from fastapi.testclient import TestClient

from eco_kb.settings import get_settings

from eco_kb.api.main import app
from eco_kb.graph.builder import build_graph
from eco_kb.kb_documents import DocumentStore
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


class FakeDocuments(DocumentStore):
    """Sin base: guarda lo que el endpoint le pide. Hereda options() y la validación."""

    def __init__(self):
        super().__init__(pool=None, client_types=["bodega", "hotel", "restaurante", "generic"])
        self.docs = {("support", "soporte/hotel/a.md"): {"audience": "support", "source_id": "soporte/hotel/a.md",
                                                        "title": "A", "client_types": ["hotel"], "chunks": 2}}
        self.calls = []

    def list(self):
        return list(self.docs.values())

    def set_visibility(self, audience, source_id, client_types, author):
        self.calls.append((audience, source_id, client_types, author))
        doc = self.docs.get((audience, source_id))
        return doc and {**doc, "client_types": client_types, "updated_by": author}


class CountingStore:
    def __init__(self):
        self.invalidated = 0

    def invalidate_catalog(self):
        self.invalidated += 1


def test_admin_documents(monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_token", "secreto")
    auth = {"Authorization": "Bearer secreto"}
    docs, kb = FakeDocuments(), CountingStore()
    monkeypatch.setattr(app.state, "documents", docs, raising=False)
    monkeypatch.setattr(app.state, "kb_stores", (kb,), raising=False)
    client = TestClient(app)

    assert client.get("/admin/documents").status_code == 401
    data = client.get("/admin/documents", headers=auth).json()
    assert data["client_types"][0] == {"id": "common", "label": "Todos"}
    assert {t["id"] for t in data["client_types"]} == {"common", "bodega", "hotel", "restaurante", "generic"}
    assert data["documents"][0]["source_id"] == "soporte/hotel/a.md"

    body = {"audience": "support", "source_id": "soporte/hotel/a.md", "client_types": ["banco"], "author": "Ana"}
    assert client.put("/admin/documents/visibility", json=body, headers=auth).status_code == 422
    assert not docs.calls and kb.invalidated == 0

    r = client.put("/admin/documents/visibility", json={**body, "client_types": ["hotel", "common"]}, headers=auth)
    assert r.status_code == 200 and r.json()["client_types"] == ["common"]  # common incluye a todos
    assert docs.calls[-1] == ("support", "soporte/hotel/a.md", ["common"], "Ana")
    assert kb.invalidated == 1  # el catálogo del reescritor se recalcula

    r = client.put("/admin/documents/visibility", json={**body, "client_types": []}, headers=auth)
    assert r.status_code == 200 and r.json()["client_types"] == []  # sin habilitar

    missing = {**body, "source_id": "soporte/hotel/no.md", "client_types": ["hotel"]}
    assert client.put("/admin/documents/visibility", json=missing, headers=auth).status_code == 404
    bad_audience = {**body, "audience": "admin", "client_types": ["hotel"]}
    assert client.put("/admin/documents/visibility", json=bad_audience, headers=auth).status_code == 422
