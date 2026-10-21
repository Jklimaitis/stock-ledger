#!/usr/bin/env bash
#
# Deploy do stock-ledger.
#
# O gate é a própria suíte: schema errado não passa daqui. Só depois de verde
# o script aplica db/*.sql no alvo e confere que o alvo ficou como declarado.
#
# Hoje o projeto não tem serviço HTTP — o que se publica é o schema. Quando a
# API existir (degrau 2), entram aqui o restart da unit e a checagem de saúde,
# no mesmo formato do deploy do home_shopper.
#
# Uso:
#   ./scripts/deploy.sh            # valida e aplica no Postgres local (docker)
#   ./scripts/deploy.sh --remoto   # valida e aplica no servidor via SSH
#   ./scripts/deploy.sh --dry-run  # mostra o plano, não altera nada

set -euo pipefail

CONTAINER="${CONTAINER:-stock-ledger-db}"
USUARIO="${USUARIO:-stock}"
BANCO="${BANCO:-stock_ledger}"

# Alvo remoto (degrau 5). Enquanto não existir banco no servidor, isto é null.
HOST="${DEPLOY_HOST:-hermes@192.168.68.61}"
KEY="${DEPLOY_KEY:-$HOME/.ssh/id_ed25519_ubuntu_hermes}"
PG_BIN="${DEPLOY_PG_BIN:-/home/hermes/pg16/usr/lib/postgresql/16/bin}"
PG_LIB="${DEPLOY_PG_LIB:-/home/hermes/pg16/usr/lib/x86_64-linux-gnu:/home/hermes/pg16/usr/lib/postgresql/16/lib}"
PG_SOCK="${DEPLOY_PG_SOCK:-/home/hermes}"
PG_ROLE="${DEPLOY_PG_ROLE:-${BANCO}}"

DRY=0
REMOTO=0
case "${1:-}" in
  --dry-run|-n) DRY=1 ;;
  --remoto)     REMOTO=1 ;;
  "")           : ;;
  *) echo "uso: $0 [--remoto] [--dry-run]" >&2; exit 64 ;;
esac

erro()   { printf '\nFALHA: %s\n' "$*" >&2; exit 1; }
titulo() { printf '\n== %s\n' "$*"; }

alvo_psql() {
  if [ "$REMOTO" -eq 1 ]; then
    ssh -o BatchMode=yes -o ConnectTimeout=10 -i "$KEY" "$HOST" \
      "export LD_LIBRARY_PATH='$PG_LIB'; '$PG_BIN/psql' -h '$PG_SOCK' -U '$PG_ROLE' -d '$BANCO' -v ON_ERROR_STOP=1 -q $*"
  else
    docker exec -i "$CONTAINER" psql -U "$USUARIO" -d "$BANCO" -v ON_ERROR_STOP=1 -q "$@"
  fi
}

alvo_nome() { [ "$REMOTO" -eq 1 ] && echo "$HOST:$BANCO" || echo "$CONTAINER:$BANCO"; }

titulo "plano"
printf '  alvo      : %s\n' "$(alvo_nome)"
printf '  migracoes : %s\n' "$(ls -1 db/*.sql | tr '\n' ' ')"
[ "$REMOTO" -eq 1 ] && printf '  modo      : remoto via SSH\n' || printf '  modo      : local (docker)\n'

if [ "$DRY" -eq 1 ]; then
  titulo "dry-run (nada foi alterado)"
  echo "  rodaria: python -m pytest -q   (o gate)"
  echo "  aplicaria db/*.sql no alvo, um por transacao"
  echo "  verificaria: 6 tabelas, a constraint critica e a guarda recusando excesso"
  exit 0
fi

# --- 1. gate -----------------------------------------------------------------
titulo "gate: suite completa"
if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi
"$PY" -m pytest -q || erro "suite vermelha — nada foi aplicado no alvo"
echo "  suite verde"

# --- 2. aplicar --------------------------------------------------------------
if [ "$REMOTO" -eq 1 ]; then
  [ -r "$KEY" ] || erro "chave SSH ausente: $KEY"
fi

titulo "aplicando schema"
if [ "$REMOTO" -eq 1 ]; then
  for arquivo in db/*.sql; do
    printf '  %-32s ' "$arquivo"
    if ! alvo_psql <"$arquivo"; then
      erro "falhou em $arquivo — o alvo pode estar em estado intermediario"
    fi
    echo "ok"
  done
else
  # O aplicador local cuida do registro de migração e da idempotência.
  ./scripts/apply_db.sh | sed 's/^/  /'
fi

# --- 3. verificar ------------------------------------------------------------
titulo "verificando o alvo"
TABELAS="$(alvo_psql -tA <<'SQL'
SELECT count(*) FROM pg_tables
 WHERE schemaname = 'public'
   AND tablename IN ('products','orders','reservations','stock_movements',
                     'product_stock','idempotency_keys');
SQL
)"
printf '  tabelas do modelo : %s de 6\n' "$TABELAS"

CONSTRAINT="$(alvo_psql -tA <<'SQL'
SELECT count(*) FROM pg_constraint
 WHERE conname = 'ck_product_stock_reserved_within_on_hand';
SQL
)"
printf '  constraint critica: %s\n' "$CONSTRAINT"

if [ "$TABELAS" != "6" ] || [ "$CONSTRAINT" != "1" ]; then
  erro "alvo nao ficou como declarado"
fi

titulo "concluido"
echo "  $(alvo_nome) pronto"
