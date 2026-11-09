# Operação

## Ambientes

| | Desenvolvimento | Teste | Produção |
| --- | --- | --- | --- |
| `AMBIENTE` | `desenvolvimento` | `teste` | `producao` |
| Banco | PostgreSQL local/Compose | `TEST_DATABASE_URL` ou PostgreSQL embutido (`pgserver`), descartável | PostgreSQL do Compose ou gerenciado |
| Cookie `Secure` | não | não | **sim** (exige HTTPS) |
| Logs | `LOG_JSON=false` facilita a leitura | — | JSON (`LOG_JSON=true`) |
| Bot | polling | processador simulado | polling (local) ou webhook (nuvem) |
| Voz | `STT_PROVEDOR=falso` dispensa o modelo | provedores falsos | `faster_whisper` |

Nunca aponte `TEST_DATABASE_URL` para um banco real: os testes apagam o esquema.

## Primeira instalação (Docker)

```bash
cp .env.example .env              # preencha POSTGRES_PASSWORD, TELEGRAM_*, WEB_SECRET_KEY
make up                           # db → migrate → web + bot
make criar-admin DOCKER=1 ARGS="--telegram-id <seu id>"
```

`DOCKER=1` faz `migrate`, `migrate-down`, `criar-admin`, `integridade`, `backup` e
`restore` usarem os contêineres (sem `make`: `docker compose run --rm web financas …`).

Sem Docker: `uv sync --extra voz`, `uv run alembic upgrade head`,
`uv run financas criar-admin`, `make web` e `make bot`.

## Logs e diagnóstico

- Saída em JSON, uma linha por evento (`ts`, `nivel`, `logger`, `msg` e campos extras),
  no stdout: `docker compose logs -f bot web`.
- Tokens do Telegram, chaves `sk-…`, senhas e a senha do `DATABASE_URL` são mascarados
  por `financas.logging.mascarar`; campos com nomes sensíveis (`senha`, `token`…) viram `***`.
- Mensagens de pessoas/chats não autorizados **não** são registradas.
- Eventos úteis: `comando` (tipo e `membro_id`, sem o texto), `voz_interpretada`,
  `transcricao_falhou`, `llm_indisponivel`, `erro_no_handler` (com `update_id`).
- Saúde da web: `GET /health`.
- Integridade: `uv run financas verificar-integridade` (código de saída 1 se houver problemas).

## Backup e restauração

```bash
make backup DOCKER=1                    # pg_dump do contêiner db → ./backups
make restore DOCKER=1 ARQUIVO=backups/financas-AAAAMMDDTHHMMSSZ.dump
# sem Docker: make backup / make restore ARQUIVO=… (pg_dump/pg_restore locais)
```

- `make restore DOCKER=1` para web e bot, restaura (`RESTORE_VIA_DOCKER=1
  scripts/restore.sh … --sim`), roda `verificar-integridade` e religa os serviços. Se
  algum passo falhar, web e bot ficam parados até você investigar
  (`docker compose start web bot`).

- Formato custom do `pg_dump`, arquivo com permissão 600, verificado com
  `pg_restore --list` antes de ser promovido; retenção `BACKUP_RETENCAO_DIAS` (30).
- A restauração roda em transação única (falhou → nada muda) e substitui os dados.
- Agendamento: `scripts/systemd/financas-backup.{service,timer}` (diário 03:30):
  ```bash
  sudo cp scripts/systemd/financas-backup.* /etc/systemd/system/
  sudo systemctl daemon-reload && sudo systemctl enable --now financas-backup.timer
  ```
- Guarde cópias fora da máquina. O teste automatizado
  `tests/integration/test_backup_restore.py` faz backup e restauração reais e compara
  dados, relatórios e integridade.

## Credenciais: criação, revogação e rotação

| Credencial | Criar | Revogar/rotacionar |
| --- | --- | --- |
| Token do bot | BotFather `/newbot` | BotFather `/revoke` → novo token no `.env` → `docker compose up -d bot` |
| Senha web | `financas criar-admin` ou tela Membros | tela Membros (nova senha encerra as sessões do membro) |
| Sessões web | login | "Sair"; desativar o membro ou trocar a senha revoga todas |
| `WEB_SECRET_KEY` | `python -c "import secrets;print(secrets.token_urlsafe(48))"` | troque e reinicie a web |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | console do provedor | revogue no console, gere outra, atualize `.env`, reinicie o bot |
| Senha do PostgreSQL | `POSTGRES_PASSWORD` antes do 1º `up` | `ALTER USER financas PASSWORD '…'` e atualize o `.env` |

O `.env` não é versionado (`.gitignore`). Na nuvem, use o gerenciador de segredos.

## Manutenção do banco

- Auditoria é somente de inserção (trigger). Para o usuário da aplicação em produção,
  recomenda-se também `REVOKE UPDATE, DELETE ON auditoria FROM <usuario_app>` quando o
  usuário dono das tabelas for separado do usuário da aplicação.
- `tentativas_login` e `confirmacoes_pendentes` crescem devagar; podem ser podadas com
  `DELETE` de registros com mais de 90 dias, se desejado.
