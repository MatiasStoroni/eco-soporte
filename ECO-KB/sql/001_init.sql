-- Esquema de la KB. Ejecutar como el usuario "ecokb" (dueño de la base) conectado a la base "ecokb",
-- DESPUÉS de 000_bootstrap.sql. No es idempotente: se ejecuta una sola vez.

CREATE TABLE kb_chunks (
    chunk_id     text        NOT NULL,
    audience     text        NOT NULL CHECK (audience IN ('support', 'sales')),
    source_id    text        NOT NULL,
    title        text        NOT NULL,
    section_path text        NOT NULL DEFAULT '',
    content      text        NOT NULL,
    client_types text[]      NOT NULL CHECK (cardinality(client_types) > 0),
    doc_type     text        NOT NULL,
    product      text        NOT NULL DEFAULT '',
    language     text        NOT NULL DEFAULT 'es',
    status       text        NOT NULL DEFAULT 'published',
    content_hash text        NOT NULL,
    embedding    vector(768) NOT NULL,
    tsv          tsvector GENERATED ALWAYS AS
                 (to_tsvector('spanish', coalesce(title, '') || ' ' || content)) STORED,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (audience, chunk_id)
) PARTITION BY LIST (audience);

CREATE TABLE kb_chunks_support PARTITION OF kb_chunks FOR VALUES IN ('support');
CREATE TABLE kb_chunks_sales   PARTITION OF kb_chunks FOR VALUES IN ('sales');

CREATE INDEX ON kb_chunks_support USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON kb_chunks_support USING gin (tsv);
CREATE INDEX ON kb_chunks_support USING gin (client_types);
CREATE INDEX ON kb_chunks_support (source_id);

CREATE INDEX ON kb_chunks_sales USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON kb_chunks_sales USING gin (tsv);
CREATE INDEX ON kb_chunks_sales USING gin (client_types);
CREATE INDEX ON kb_chunks_sales (source_id);

-- Aislamiento: cada rol de lectura solo ve su partición.
REVOKE ALL ON kb_chunks, kb_chunks_support, kb_chunks_sales FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO rag_support_ro, rag_sales_ro;
GRANT SELECT ON kb_chunks_support TO rag_support_ro;
GRANT SELECT ON kb_chunks_sales   TO rag_sales_ro;
