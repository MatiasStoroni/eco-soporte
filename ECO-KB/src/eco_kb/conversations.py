"""Registro de conversaciones para el panel de administración y la derivación a humano.

El historial que usa el grafo sigue en el checkpointer de LangGraph; esto es una copia legible, turno a turno,
con los metadatos de cada respuesta (intent, fallback, fuentes, audit_log, latencia) y la revisión del equipo.
Usa el pool admin: los roles de solo lectura del RAG no tienen acceso a estas tablas.

Estados de una conversación:
  bot     → responde el bot (normal).
  pending → se pidió una persona (o el bot no pudo responder varias veces). El bot SIGUE respondiendo.
  human   → alguien del equipo la tomó desde el panel: el bot no responde hasta que se la devuelvan.
"""
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

STATUSES = ("bot", "pending", "human")
ANSWER_FALLBACKS = ("no_documents", "ungrounded", "answer_mismatch")  # el bot no pudo responder

SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_conversations (
    session_id     text        PRIMARY KEY,
    is_registered  boolean     NOT NULL,
    flow           text        NOT NULL,
    client_type    text        NOT NULL,
    language       text        NOT NULL DEFAULT 'es',
    status         text        NOT NULL DEFAULT 'bot' CHECK (status IN ('bot', 'pending', 'human')),
    handoff_reason text,
    handoff_at     timestamptz,
    assignee       text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chat_conversations_updated_idx ON chat_conversations (updated_at DESC);

CREATE TABLE IF NOT EXISTS chat_messages (
    id              bigserial   PRIMARY KEY,
    session_id      text        NOT NULL REFERENCES chat_conversations ON DELETE CASCADE,
    role            text        NOT NULL CHECK (role IN ('user', 'bot', 'human', 'system')),
    content         text        NOT NULL,
    author          text,
    flow            text,
    intent          text,
    fallback_reason text,
    sources         jsonb       NOT NULL DEFAULT '[]',
    cta_url         text,
    latency_ms      integer,
    audit_log       jsonb,
    rating          text        CHECK (rating IN ('good', 'bad')),
    review_note     text,
    reviewed_by     text,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chat_messages_session_idx ON chat_messages (session_id, id);
"""

_PUBLIC_COLS = "id, role, content, author, created_at"


class ConversationStore:
    def __init__(self, pool: ConnectionPool):
        self.pool = pool

    def setup(self) -> None:
        """Idempotente: crea las tablas si no existen (también en bases ya inicializadas)."""
        with self.pool.connection() as conn:
            conn.execute(SCHEMA)

    # --- turnos del chat -------------------------------------------------------------------------------

    def start_turn(self, req) -> str:
        """Crea/actualiza la conversación, guarda el mensaje del usuario y devuelve el estado actual."""
        flow = "support" if req.is_registered else "sales"
        with self.pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """INSERT INTO chat_conversations (session_id, is_registered, flow, client_type, language)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (session_id) DO UPDATE SET is_registered = EXCLUDED.is_registered,
                       flow = EXCLUDED.flow, client_type = EXCLUDED.client_type, language = EXCLUDED.language,
                       updated_at = now()
                   RETURNING status""",
                (req.session_id, req.is_registered, flow, req.client_type, req.language),
            ).fetchone()
            conn.execute(
                "INSERT INTO chat_messages (session_id, role, content, flow) VALUES (%s, 'user', %s, %s)",
                (req.session_id, req.message, flow),
            )
        return row["status"]

    def log_bot(self, session_id: str, resp, latency_ms: int) -> int:
        with self.pool.connection() as conn:
            row = conn.execute(
                """INSERT INTO chat_messages (session_id, role, content, flow, intent, fallback_reason, sources,
                                              cta_url, latency_ms, audit_log)
                   VALUES (%s, 'bot', %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (session_id, resp.answer, resp.flow, resp.intent, resp.fallback_reason,
                 Jsonb([s.model_dump() for s in resp.sources]), resp.cta_url, latency_ms, Jsonb(resp.audit_log)),
            ).fetchone()
        return row["id"]

    def log_system(self, session_id: str, content: str) -> int:
        with self.pool.connection() as conn:
            row = conn.execute(
                "INSERT INTO chat_messages (session_id, role, content) VALUES (%s, 'system', %s) RETURNING id",
                (session_id, content),
            ).fetchone()
        return row["id"]

    def trailing_fallbacks(self, session_id: str, reasons: list[str]) -> int:
        """Cuántas respuestas del bot seguidas (las últimas) fueron fallbacks de `reasons`."""
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT fallback_reason FROM chat_messages WHERE session_id = %s AND role = 'bot' "
                "ORDER BY id DESC LIMIT 50", (session_id,),
            ).fetchall()
        n = 0
        for r in rows:
            if r["fallback_reason"] not in reasons:
                break
            n += 1
        return n

    def request_handoff(self, session_id: str, reason: str) -> bool:
        """bot → pending. Devuelve True si cambió (no repite la derivación si ya estaba pendiente o tomada)."""
        with self.pool.connection() as conn:
            row = conn.execute(
                """UPDATE chat_conversations SET status = 'pending', handoff_reason = %s, handoff_at = now(),
                          updated_at = now()
                   WHERE session_id = %s AND status = 'bot' RETURNING session_id""",
                (reason, session_id),
            ).fetchone()
        return row is not None

    def updates(self, session_id: str, after: int) -> dict | None:
        """Lo que la vista del chat necesita para mostrar mensajes del equipo (polling)."""
        with self.pool.connection() as conn:
            conv = conn.execute(
                "SELECT status, assignee FROM chat_conversations WHERE session_id = %s", (session_id,),
            ).fetchone()
            if conv is None:
                return None
            msgs = conn.execute(
                f"SELECT {_PUBLIC_COLS} FROM chat_messages WHERE session_id = %s AND id > %s "
                "AND role IN ('human', 'system') ORDER BY id",
                (session_id, after),
            ).fetchall()
        return {"status": conv["status"], "assignee": conv["assignee"], "messages": msgs}

    # --- panel de administración ----------------------------------------------------------------------

    def stats(self) -> dict:
        with self.pool.connection() as conn:
            conv = conn.execute(
                """SELECT count(*) AS total,
                          count(*) FILTER (WHERE created_at > now() - interval '24 hours') AS last_24h,
                          count(*) FILTER (WHERE status = 'pending') AS pending,
                          count(*) FILTER (WHERE status = 'human') AS human
                   FROM chat_conversations""",
            ).fetchone()
            msg = conn.execute(
                """SELECT count(*) AS answers,
                          count(*) FILTER (WHERE fallback_reason = ANY(%s)) AS unanswered,
                          count(*) FILTER (WHERE fallback_reason = 'safety') AS safety,
                          count(*) FILTER (WHERE rating = 'bad') AS bad_reviews,
                          count(*) FILTER (WHERE rating = 'good') AS good_reviews,
                          round(avg(latency_ms))::int AS avg_latency_ms
                   FROM chat_messages WHERE role = 'bot'""",
                (list(ANSWER_FALLBACKS),),
            ).fetchone()
        return {**conv, **msg}

    def list(self, *, status=None, flow=None, client_type=None, q=None, issues=False,
             limit: int = 50, offset: int = 0) -> list[dict]:
        where, params = [], []
        if status:
            where.append("c.status = %s")
            params.append(status)
        if flow:
            where.append("c.flow = %s")
            params.append(flow)
        if client_type:
            where.append("c.client_type = %s")
            params.append(client_type)
        if q:
            where.append("(c.session_id ILIKE %s OR EXISTS (SELECT 1 FROM chat_messages m "
                         "WHERE m.session_id = c.session_id AND m.content ILIKE %s))")
            params += [f"%{q}%", f"%{q}%"]
        if issues:
            where.append("(c.status <> 'bot' OR EXISTS (SELECT 1 FROM chat_messages m WHERE m.session_id = "
                         "c.session_id AND (m.fallback_reason = ANY(%s) OR m.rating = 'bad')))")
            params.append(list(ANSWER_FALLBACKS))
        sql = f"""
            SELECT c.*, s.turns, s.unanswered, s.bad_reviews, s.safety, last.content AS last_message,
                   last.role AS last_role
            FROM chat_conversations c
            CROSS JOIN LATERAL (
                SELECT count(*) FILTER (WHERE role = 'user') AS turns,
                       count(*) FILTER (WHERE fallback_reason = ANY(%s)) AS unanswered,
                       count(*) FILTER (WHERE rating = 'bad') AS bad_reviews,
                       count(*) FILTER (WHERE fallback_reason = 'safety') AS safety
                FROM chat_messages WHERE session_id = c.session_id) s
            LEFT JOIN LATERAL (
                SELECT content, role FROM chat_messages
                WHERE session_id = c.session_id AND role IN ('user', 'human')
                ORDER BY id DESC LIMIT 1) last ON true
            {"WHERE " + " AND ".join(where) if where else ""}
            ORDER BY (c.status = 'pending') DESC, c.updated_at DESC
            LIMIT %s OFFSET %s"""
        with self.pool.connection() as conn:
            return conn.execute(sql, [list(ANSWER_FALLBACKS), *params, limit, offset]).fetchall()

    def get(self, session_id: str) -> dict | None:
        with self.pool.connection() as conn:
            conv = conn.execute("SELECT * FROM chat_conversations WHERE session_id = %s", (session_id,)).fetchone()
            if conv is None:
                return None
            conv["messages"] = conn.execute(
                "SELECT * FROM chat_messages WHERE session_id = %s ORDER BY id", (session_id,),
            ).fetchall()
        return conv

    def set_status(self, session_id: str, status: str, author: str | None) -> dict | None:
        """Acciones del panel: tomar (→human), devolver al bot o descartar la derivación (→bot)."""
        with self.pool.connection() as conn, conn.transaction():
            prev = conn.execute(
                "SELECT status FROM chat_conversations WHERE session_id = %s FOR UPDATE", (session_id,),
            ).fetchone()
            if prev is None:
                return None
            if prev["status"] == status:
                return {"status": status, "changed": False}
            if status == "human":
                conn.execute(
                    "UPDATE chat_conversations SET status = 'human', assignee = %s, updated_at = now() "
                    "WHERE session_id = %s", (author, session_id),
                )
                note = f"{author or 'Una persona del equipo'} se sumó a la conversación."
            else:  # bot
                conn.execute(
                    "UPDATE chat_conversations SET status = 'bot', assignee = NULL, handoff_reason = NULL, "
                    "handoff_at = NULL, updated_at = now() WHERE session_id = %s", (session_id,),
                )
                # Descartar una derivación pendiente no se anuncia al usuario; devolver una tomada, sí.
                note = "La conversación vuelve al asistente virtual." if prev["status"] == "human" else None
            if note:
                conn.execute(
                    "INSERT INTO chat_messages (session_id, role, content, author) VALUES (%s, 'system', %s, %s)",
                    (session_id, note, author),
                )
        return {"status": status, "changed": True}

    def add_human_message(self, session_id: str, content: str, author: str | None) -> dict | None:
        with self.pool.connection() as conn:
            conv = conn.execute(
                "UPDATE chat_conversations SET updated_at = now() WHERE session_id = %s RETURNING flow",
                (session_id,),
            ).fetchone()
            if conv is None:
                return None
            return conn.execute(
                f"INSERT INTO chat_messages (session_id, role, content, author, flow) VALUES (%s, 'human', %s, %s, %s) "
                f"RETURNING {_PUBLIC_COLS}",
                (session_id, content, author, conv["flow"]),
            ).fetchone()

    def review(self, message_id: int, rating: str | None, note: str | None, author: str | None) -> dict | None:
        with self.pool.connection() as conn:
            return conn.execute(
                """UPDATE chat_messages SET rating = %s, review_note = %s, reviewed_by = %s
                   WHERE id = %s AND role = 'bot' RETURNING id, rating, review_note, reviewed_by""",
                (rating, note or None, author, message_id),
            ).fetchone()
