#!/bin/bash
# Solo para el Postgres LOCAL de desarrollo (compose.local-db.yml). Contraseñas de desarrollo.
set -euo pipefail
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres \
  -v admin_user=ecokb -v admin_pw=ecokb -v support_pw=support_ro_dev -v sales_pw=sales_ro_dev -f /sql/000_bootstrap.sql
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d ecokb -f /sql/001_init.sql
