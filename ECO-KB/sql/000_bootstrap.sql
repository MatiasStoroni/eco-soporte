-- Crea (si no existen) el usuario admin, los dos roles de solo lectura, la base de datos y la extensión.
-- Ejecutar como SUPERUSUARIO conectado a la base "postgres" (ver scripts/bootstrap-db.sh):
--   psql -U <superusuario> -d postgres -v admin_pw=... -v support_pw=... -v sales_pw=... -f sql/000_bootstrap.sql
-- Es idempotente: si algo ya existe, lo omite (no cambia contraseñas existentes).
\set ON_ERROR_STOP on

SELECT format('CREATE ROLE ecokb LOGIN PASSWORD %L', :'admin_pw')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ecokb') \gexec
SELECT format('CREATE ROLE rag_support_ro LOGIN PASSWORD %L', :'support_pw')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rag_support_ro') \gexec
SELECT format('CREATE ROLE rag_sales_ro LOGIN PASSWORD %L', :'sales_pw')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rag_sales_ro') \gexec

SELECT 'CREATE DATABASE ecokb OWNER ecokb'
 WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'ecokb') \gexec

-- Los roles de lectura no deben poder tocar ninguna otra base del servidor compartido.
REVOKE ALL ON DATABASE ecokb FROM PUBLIC;
GRANT CONNECT ON DATABASE ecokb TO ecokb, rag_support_ro, rag_sales_ro;

\connect ecokb
CREATE EXTENSION IF NOT EXISTS vector;
