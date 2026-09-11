#!/usr/bin/env bash

set -euo pipefail

if [ -z "${RUN_DIR:-}" ]; then
    echo "Informe RUN_DIR. Exemplo: RUN_DIR=runs/Acme/20260910-220000 ./rodar2.sh" >&2
    exit 1
fi

SQL_DIR="$RUN_DIR/sql"
LOG_DIR="$RUN_DIR/logs"
ERROR_DIR="$RUN_DIR/erros"

DB_HOST="${PGHOST:-localhost}"
DB_PORT="${PGPORT:-4003}"
DB_NAME="${PGDATABASE:-tenant-demo}"
DB_USER="${PGUSER:-tenant-demo}"

mkdir -p "$LOG_DIR" "$ERROR_DIR"

run_sql() {
    local filename="$1"
    local sql_file="$SQL_DIR/$filename"
    local log_file="$LOG_DIR/$filename.log"

    if [ ! -f "$sql_file" ]; then
        echo "Arquivo SQL não encontrado: $sql_file" >&2
        exit 1
    fi

    date
    psql -a -h "$DB_HOST" -d "$DB_NAME" -p "$DB_PORT" -U "$DB_USER" -f "$sql_file" > "$log_file" 2>&1
    date
}

extract_errors() {
    local filename="$1"
    local log_file="$LOG_DIR/$filename.log"
    local error_file="$ERROR_DIR/${filename%.sql}_contexto.txt"

    grep "ERROR:\|WARNING:" -B 10 -A 10 "$log_file" > "$error_file" || true
}

run_sql "00_setup.sql"
run_sql "01_prepare_target.sql"
run_sql "02_dump_compatible_tables.sql"
run_sql "03_load_transformed_data.sql"
run_sql "04_finalize_target.sql"

extract_errors "00_setup.sql"
extract_errors "01_prepare_target.sql"
extract_errors "02_dump_compatible_tables.sql"
extract_errors "03_load_transformed_data.sql"
extract_errors "04_finalize_target.sql"
