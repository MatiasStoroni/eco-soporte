#!/bin/bash
# Init de los contenedores pgvector propios (compose.local-db.yml y "eco-db" en ../compose.yml).
# Solo corre con el volumen vacío. El admin es POSTGRES_USER; contraseñas de lectura por entorno.
set -euo pipefail
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres \
  -v admin_user="$POSTGRES_USER" -v admin_pw="$POSTGRES_PASSWORD" \
  -v support_pw="${ECOKB_SUPPORT_PASSWORD:-support_ro_dev}" -v sales_pw="${ECOKB_SALES_PASSWORD:-sales_ro_dev}" \
  -f /sql/000_bootstrap.sql
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d ecokb -f /sql/001_init.sql
