#!/usr/bin/env bash
# Restaura um backup gerado por backup.sh. APAGA o conteúdo atual do banco de destino.
#
# Uso:  scripts/restore.sh <arquivo.dump> --sim
# Variáveis:
#   RESTORE_DATABASE_URL   banco de destino (padrão: DATABASE_URL)
#   PG_BIN                 diretório dos binários (opcional)
#   RESTORE_VIA_DOCKER=1   usa o pg_restore do contêiner "db" do compose.yaml
#
# A restauração roda em transação única: se algo falhar, nada muda.
# Depois, rode:  uv run financas verificar-integridade
set -euo pipefail

arquivo="${1:?uso: restore.sh <arquivo.dump> --sim}"
if [[ "${2:-}" != "--sim" ]]; then
  echo "Isto substitui todos os dados do banco de destino. Repita com --sim para confirmar." >&2
  exit 2
fi
[[ -r "$arquivo" ]] || { echo "Arquivo não encontrado: $arquivo" >&2; exit 1; }

raiz="$(cd "$(dirname "$0")/.." && pwd)"
if [[ "${RESTORE_VIA_DOCKER:-0}" == "1" ]]; then
  db=(docker compose -f "$raiz/compose.yaml" exec -T db)
  "${db[@]}" pg_restore --list < "$arquivo" > /dev/null
  "${db[@]}" pg_restore --clean --if-exists --no-owner --no-privileges \
    --single-transaction --exit-on-error -U financas -d financas < "$arquivo"
  echo "Restaurado: $arquivo"
  exit 0
fi
if [[ -z "${RESTORE_DATABASE_URL:-}${DATABASE_URL:-}" && -f "$raiz/.env" ]]; then
  DATABASE_URL="$(grep -E '^DATABASE_URL=' "$raiz/.env" | tail -1 | cut -d= -f2- | sed 's/[[:space:]]*#.*$//')"
fi
url="${RESTORE_DATABASE_URL:-${DATABASE_URL:?defina DATABASE_URL ou RESTORE_DATABASE_URL}}"
url="${url/postgresql+psycopg:/postgresql:}"
bin="${PG_BIN:+$PG_BIN/}"

"${bin}pg_restore" --list "$arquivo" > /dev/null
"${bin}pg_restore" --clean --if-exists --no-owner --no-privileges \
  --single-transaction --exit-on-error --dbname="$url" "$arquivo"
echo "Restaurado: $arquivo"
