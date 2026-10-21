#!/usr/bin/env bash
#
# Aplica db/*.sql, em ordem, no banco de desenvolvimento — uma vez cada.
#
# Controle de migração: a tabela `schema_migrations` guarda o nome de cada
# arquivo já aplicado. Arquivo registrado é pulado, então rodar este script
# duas vezes é seguro e não tenta recriar tabela existente.
#
# Cada arquivo e o registro da sua versão entram na MESMA transação
# (--single-transaction): se o DDL falhar no meio, nem as tabelas nem o
# registro ficam. Sem isso, um arquivo aplicado pela metade ficaria marcado
# como concluído.
#
# Uso:
#   ./scripts/apply_db.sh
#   BANCO=outro_banco ./scripts/apply_db.sh
#   ./scripts/apply_db.sh --status      # só lista o que está aplicado

set -euo pipefail

CONTAINER="${CONTAINER:-stock-ledger-db}"
USUARIO="${USUARIO:-stock}"
BANCO="${BANCO:-stock_ledger}"

psql_alvo() {
  docker exec -i "$CONTAINER" psql -U "$USUARIO" -d "$BANCO" -v ON_ERROR_STOP=1 "$@"
}

if ! docker ps --filter "name=$CONTAINER" --format '{{.Names}}' | grep -q .; then
  echo "container $CONTAINER nao esta rodando — rode: docker compose up -d" >&2
  exit 1
fi

# A tabela de controle é criada pelo runner, não por uma migração: ela precisa
# existir antes da primeira.
psql_alvo -q -c "
SET client_min_messages = warning;
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    text        NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_schema_migrations PRIMARY KEY (version)
);" >/dev/null

if [ "${1:-}" = "--status" ]; then
  echo "migracoes em db/:"
  for arquivo in db/*.sql; do
    versao="$(basename "$arquivo")"
    if [ "$(psql_alvo -tAc "SELECT count(*) FROM schema_migrations WHERE version = '$versao'")" = "1" ]; then
      printf '  [x] %s\n' "$versao"
    else
      printf '  [ ] %s\n' "$versao"
    fi
  done
  exit 0
fi

for arquivo in db/*.sql; do
  versao="$(basename "$arquivo")"
  aplicada="$(psql_alvo -tAc "SELECT count(*) FROM schema_migrations WHERE version = '$versao'")"

  if [ "$aplicada" = "1" ]; then
    printf '%-32s ja aplicada\n' "$versao"
    continue
  fi

  printf '%-32s aplicando ... ' "$versao"
  # DDL e registro na mesma transação, por stdin.
  {
    cat "$arquivo"
    printf "\nINSERT INTO schema_migrations (version) VALUES ('%s');\n" "$versao"
  } | psql_alvo -q --single-transaction
  echo "ok"
done

echo
echo "tabelas no banco $BANCO:"
psql_alvo -tAc "SELECT '  ' || tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
