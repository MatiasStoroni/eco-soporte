"""Requiere la base local (compose.local-db.yml). Crea sesiones de prueba y las borra al terminar."""
import uuid

import pytest

from eco_kb.api.schemas import ChatResponse
from eco_kb.conversations import ConversationStore
from eco_kb.db import make_pool
from eco_kb.graph.state import SessionPayload
from eco_kb.settings import get_settings

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def store():
    pool = make_pool(get_settings().database_url_admin, "test_conv")
    s = ConversationStore(pool)
    s.setup()
    s.setup()  # idempotente
    yield s
    with pool.connection() as conn:
        conn.execute("DELETE FROM chat_conversations WHERE session_id LIKE 'test-%'")
    pool.close()


def turn(store, sid, msg, fallback=None):
    req = SessionPayload(session_id=sid, is_registered=True, client_type="hotel", message=msg)
    status = store.start_turn(req)
    resp = ChatResponse(answer="r", flow="support", intent="business_question", sources=[],
                        fallback_reason=fallback, cta_url=None, audit_log=[{"event": "turn_start"}])
    return status, store.log_bot(sid, resp, 1234)


def test_lifecycle(store):
    sid = f"test-{uuid.uuid4().hex}"
    assert turn(store, sid, "hola")[0] == "bot"
    turn(store, sid, "a", "no_documents")
    turn(store, sid, "b", "no_documents")
    assert store.trailing_fallbacks(sid, ["no_documents"]) == 2

    assert store.request_handoff(sid, "repeated_fallback") is True
    assert store.request_handoff(sid, "user_request") is False  # ya estaba pendiente
    assert any(c["session_id"] == sid for c in store.list(status="pending"))

    assert store.set_status(sid, "human", "Ana") == {"status": "human", "changed": True}
    assert turn(store, sid, "sigo acá")[0] == "human"
    human = store.add_human_message(sid, "Hola, soy Ana", "Ana")
    up = store.updates(sid, 0)
    assert up["status"] == "human" and [m["role"] for m in up["messages"]] == ["system", "human"]
    assert store.updates(sid, human["id"])["messages"] == []

    store.set_status(sid, "bot", "Ana")
    conv = store.get(sid)
    assert conv["status"] == "bot" and conv["handoff_reason"] is None
    assert conv["messages"][-1]["role"] == "system"  # "vuelve al asistente"

    bot_id = conv["messages"][1]["id"]
    assert store.review(bot_id, "bad", "inventó", "Ana")["rating"] == "bad"
    assert store.review(conv["messages"][0]["id"], "bad", None, "Ana") is None  # solo mensajes del bot
    assert store.list(q="sigo acá", issues=True)[0]["session_id"] == sid
    assert store.stats()["total"] >= 1
