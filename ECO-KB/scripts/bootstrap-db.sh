#!/usr/bin/env bash
# Crea la base "ecokb", sus usuarios y el esquema en el Postgres COMPARTIDO del servidor.
# Ejecutar UNA vez, en el servidor, desde la raíz del repo:
#
#   PG_SUPERUSER=<superusuario> ECOKB_SUPPORT_PASSWORD=... ECOKB_SALES_PASSWORD=... \
#   bash scripts/bootstrap-db.sh
#
# Por defecto: contenedor "postgres-server", admin "eco" con contraseña "eco" (anulables con
# PG_CONTAINER, ECOKB_ADMIN_USER, ECOKB_ADMIN_PASSWORD). Si el admin ya existe, no se cambia su contraseña.
# Dentro del contenedor, psql usa el socket local (el superusuario no necesita contraseña).
set -euo pipefail
: "${PG_CONTAINER:=postgres-server}"
: "${PG_SUPERUSER:?superusuario de postgres}"
: "${ECOKB_ADMIN_USER:=eco}"
: "${ECOKB_ADMIN_PASSWORD:=eco}"
: "${ECOKB_SUPPORT_PASSWORD:?}"
: "${ECOKB_SALES_PASSWORD:?}"

docker exec -i "$PG_CONTAINER" psql -U "$PG_SUPERUSER" -d postgres -v ON_ERROR_STOP=1 \
  -v admin_user="$ECOKB_ADMIN_USER" -v admin_pw="$ECOKB_ADMIN_PASSWORD" \
  -v support_pw="$ECOKB_SUPPORT_PASSWORD" -v sales_pw="$ECOKB_SALES_PASSWORD" \
  < sql/000_bootstrap.sql

docker exec -i -e PGPASSWORD="$ECOKB_ADMIN_PASSWORD" "$PG_CONTAINER" \
  psql -h 127.0.0.1 -U "$ECOKB_ADMIN_USER" -d ecokb -v ON_ERROR_STOP=1 < sql/001_init.sql

echo "OK: base ecokb lista. Usuarios: $ECOKB_ADMIN_USER (admin), rag_support_ro, rag_sales_ro."
