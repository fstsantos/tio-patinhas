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
- Eventos úteis: `comando`, `voz_interpretada` (com `via_llm`), `transcricao_falhou`,
  `llm_chamada` (provedor, modelo e consumo de tokens de cada chamada),
  `llm_indisponivel`, `erro_no_handler` (com `update_id`).
- Saúde da web: `GET /health`.
- Integridade: `uv run financas verificar-integridade` (código de saída 1 se houver problemas).

## LLM para interpretar áudios (opcional)

O áudio é transcrito e interpretado na própria máquina. Só quando isso falha — fala
livre, data por extenso, vários lançamentos no mesmo áudio — a transcrição é enviada a
uma LLM. Desligada por padrão.

Ligar (no `.env`, depois `docker compose up -d --force-recreate bot`):

```bash
# Claude (padrão do provedor: claude-haiku-5-5)
LLM_PROVEDOR=anthropic
LLM_API_KEY=<chave>

# OpenAI ou qualquer serviço compatível com "chat completions"
LLM_PROVEDOR=openai
LLM_MODELO=<modelo>
LLM_API_KEY=<chave>
LLM_BASE_URL=<endereço do serviço>   # vazio = https://api.openai.com/v1
```

Trocar de provedor é mudar essas linhas. Validar antes de usar no grupo (não grava
nada; aqui a LLM é sempre chamada):

```bash
make testar-llm DOCKER=1 FRASE="um débito de 20 de cerveja e outro de 15 de petisco"
```

- Custo: cada chamada envia cerca de 700 tokens e recebe menos de 200; o evento
  `llm_chamada` nos logs do bot registra o consumo real de cada uma.
- Privacidade: saem da máquina a transcrição e a data de hoje; nada do banco.
- Sem internet, com a chave errada ou com a LLM lenta (`LLM_TIMEOUT_SEGUNDOS`), o bot
  responde com o resultado local, como se a LLM estivesse desligada.
- Novo provedor que não seja compatível: uma classe com `completar_json` em
  `integrations/llm/` e um `case` em `integrations/llm/__init__.py`.

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

## Importação de fatura de cartão (Itaú)

Exporte a fatura **fechada** em `.xlsx` pelo site do Itaú e guarde em `faturas/` (a
pasta e qualquer `*.xlsx` ficam fora do git: a planilha traz agência e conta).

```bash
make backup DOCKER=1
# prévia: mostra cada linha, a categoria e a situação; não grava nada
make importar-fatura DOCKER=1 ARQUIVO="faturas/<arquivo>.xlsx" \
  ARGS="--membro <nome> --regras faturas/regras-itau.toml"
# grava (uma única transação) e confere
make importar-fatura DOCKER=1 ARQUIVO="faturas/<arquivo>.xlsx" \
  ARGS="--membro <nome> --regras faturas/regras-itau.toml --confirmar"
make integridade DOCKER=1
```

- Cada linha vira uma compra no crédito do membro informado (que também é o autor, com
  origem `CLI`). O pagamento da fatura anterior é descartado; a soma das demais linhas
  precisa bater com o total da fatura.
- Parcelas: "Parcela *k* de *N*" vira a compra inteira (parcela × *N*), com a 1ª parcela
  no mês da fatura − (*k* − 1). Parcelados antigos criam parcelas em meses passados.
- Regras (`--regras`, TOML; exemplo em `docs/exemplos/regras-fatura.toml`): `ignorar`
  descarta linhas; `[[categoria]]` define a categoria por trecho da descrição.
  Categorias que não existem são criadas (exige membro administrador). Linha sem regra
  entra sem categoria.
- Valor negativo (estorno) sem regra em `ignorar` é erro: nada é gravado até você
  decidir (ignorar a linha ou lançar à mão).
- Pode repetir à vontade: compras já importadas aparecem como "já existe". A comparação
  é por titular, data, descrição, parcelas e valor — se você editar um desses campos
  depois, a próxima importação verá a compra como nova (confira a prévia).
- Cobrança de recorrência de crédito (assinatura): a linha à vista de mesmo valor, com
  data a até 5 dias da compra gerada pela recorrência, aparece como "já existe" com
  `#id (recorrência de dd/mm)`. Se o valor mudou, entra como nova: cancele o lançamento
  estimado daquele mês.
- A linha "Parcelas desta fatura em <mês>" deve ser igual ao total da fatura menos o que
  foi ignorado.
- O nome do estabelecimento vem colado à cidade ("Petshopsao Paulobra"); a limpeza
  conhece as cidades mais comuns (`CIDADES_PADRAO` em `integrations/fatura/itau.py`).
  Cidade desconhecida fica na descrição — acrescente-a à lista.

## Recorrências

O processo web lança sozinho as recorrências do mês: confere ao subir e a cada hora.
Não há nada a agendar. Se a web ficar parada na virada do mês, os lançamentos entram
quando ela voltar; para forçar na hora, `make recorrentes` (`DOCKER=1` no Compose).
Recorrência que não pôde ser gerada aparece no log da web como
`recorrencia_nao_gerada` (com o motivo) e é tentada de novo na hora seguinte.

## Manutenção do banco

- Auditoria é somente de inserção (trigger). Para o usuário da aplicação em produção,
  recomenda-se também `REVOKE UPDATE, DELETE ON auditoria FROM <usuario_app>` quando o
  usuário dono das tabelas for separado do usuário da aplicação.
- `tentativas_login` e `confirmacoes_pendentes` crescem devagar; podem ser podadas com
  `DELETE` de registros com mais de 90 dias, se desejado.
