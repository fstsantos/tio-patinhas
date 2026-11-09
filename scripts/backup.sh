#!/usr/bin/env bash
# Backup do banco com pg_dump (formato custom), verificação e retenção.
#
# Uso:  scripts/backup.sh [diretório]
# Variáveis:
#   DATABASE_URL           banco de origem (lida também do .env, se existir)
#   BACKUP_DIR             destino (padrão ./backups; o argumento tem precedência)
#   BACKUP_RETENCAO_DIAS   apaga backups mais antigos que N dias (padrão 30)
#   BACKUP_VIA_DOCKER=1    usa o pg_dump do contêiner "db" do compose.yaml
#   PG_BIN                 diretório dos binários pg_dump/pg_restore (opcional)
set -euo pipefail

raiz="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -z "${DATABASE_URL:-}" && -f "$raiz/.env" ]]; then
  DATABASE_URL="$(grep -E '^DATABASE_URL=' "$raiz/.env" | tail -1 | cut -d= -f2- | sed 's/[[:space:]]*#.*$//')"
fi
destino="${1:-${BACKUP_DIR:-$raiz/backups}}"
retencao="${BACKUP_RETENCAO_DIAS:-30}"
bin="${PG_BIN:+$PG_BIN/}"

umask 077
mkdir -p "$destino"
arquivo="$destino/financas-$(date -u +%Y%m%dT%H%M%SZ).dump"
parcial="$arquivo.parcial"
trap 'rm -f "$parcial"' EXIT

if [[ "${BACKUP_VIA_DOCKER:-0}" == "1" ]]; then
  docker compose -f "$raiz/compose.yaml" exec -T db \
    pg_dump --format=custom --no-owner --no-privileges -U financas financas > "$parcial"
  docker compose -f "$raiz/compose.yaml" exec -T db pg_restore --list < "$parcial" > /dev/null
else
  url="${DATABASE_URL:?defina DATABASE_URL}"
  url="${url/postgresql+psycopg:/postgresql:}"
  "${bin}pg_dump" --format=custom --no-owner --no-privileges --dbname="$url" --file="$parcial"
  "${bin}pg_restore" --list "$parcial" > /dev/null  # o arquivo é legível
fi

mv "$parcial" "$arquivo"
trap - EXIT
find "$destino" -maxdepth 1 -name 'financas-*.dump' -type f -mtime +"$retencao" -delete
echo "$arquivo"
