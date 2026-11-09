# Tio Patinhas — Gerenciador Financeiro Familiar

Bot do Telegram + aplicação web para a família registrar receitas, despesas e compras
parceladas, acompanhar o orçamento do mês e consultar compromissos futuros.

> **Estado:** Etapas 2 (fundação), 3 (bot) e 4 (web) implementadas; operação e
> integrações (Etapa 5) em andamento — veja `PROGRESSO.md`.

## Pré-requisitos

- Linux com [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- Docker + Docker Compose (para execução local completa)
- ffmpeg (mensagens de voz)

## Desenvolvimento

```bash
uv sync                 # instala Python 3.12 e dependências travadas
cp .env.example .env    # preencha as variáveis
uv run pytest           # testes (PostgreSQL embutido se TEST_DATABASE_URL não existir)
make check              # ruff + mypy + testes com cobertura
```

Sem `make`, use os comandos do `Makefile` diretamente (`uv run ruff check ...`).

## Banco de dados e migrações

```bash
uv run alembic upgrade head      # aplica (DATABASE_URL do .env)
uv run alembic downgrade -1      # reverte a última
uv run financas criar-admin      # primeiro administrador
uv run financas verificar-integridade
```

O esquema é criado somente por migrações Alembic (`migrations/versions/`).

## Bot do Telegram

### Criação e configuração

1. No [@BotFather](https://t.me/BotFather): `/newbot`, escolha nome e username; copie o
   token para `TELEGRAM_BOT_TOKEN` e o username para `TELEGRAM_BOT_USERNAME`.
2. **Desative o privacy mode antes de adicionar o bot ao grupo:** `/setprivacy` →
   escolha o bot → `Disable`. Se o bot já estiver no grupo, remova-o e adicione de novo.
   Sem isso, o bot só recebe comandos com `/` e não recebe `débito …` nem áudios.
3. Adicione o bot ao grupo da família e descubra o `chat_id` do grupo (número negativo;
   por exemplo, enviando uma mensagem e consultando
   `https://api.telegram.org/bot<TOKEN>/getUpdates`). Coloque-o em
   `TELEGRAM_CHATS_PERMITIDOS` (vários separados por vírgula).
4. Cadastre cada membro com o **id numérico do Telegram** (pela web, Etapa 4, ou
   `financas criar-admin --telegram-id <id>` para o administrador). Estar no grupo não
   autoriza ninguém: a autorização vem só do cadastro.

Mensagens de chats não permitidos ou de pessoas não cadastradas são ignoradas sem que
o conteúdo seja registrado nos logs.

### Execução

```bash
uv sync --extra voz            # inclui faster-whisper (transcrição local)
uv run python -m financas.bot.app
```

Sem `TELEGRAM_WEBHOOK_URL` o bot usa *polling*; com ela, *webhook* (nuvem).

### Comandos

| Comando | Exemplo |
| --- | --- |
| `débito <valor> <descrição> [data]` | `débito 10,50 compras de mercado` · `débito 45 farmácia ontem` |
| `crédito <total> <parcelas> <descrição> [data]` | `crédito 1000,00 10x mercado livre` |
| `ganho <valor> <descrição> [data]` | `ganho 3500 salário 05/03` |
| `resumo mês [mm/aaaa]` | `resumo mês 02/2026` |
| `resumo ano [aaaa]` | `resumo ano 2026` |
| `resumo crédito` | parcelas pendentes do mês atual em diante |
| `últimos` | seus 10 lançamentos ativos mais recentes |
| `cancelar <código> [motivo]` | `cancelar 42 lançado em dobro` (pede confirmação) |
| `desfazer` | cancela seu último lançamento dos últimos 10 min (pede confirmação) |
| `ajuda` / `help` / `start` | |

Palavras-chave sem distinção de acentos e maiúsculas; aceita `/debito` e
`/debito@SeuBot`. Data opcional: `ontem`, `dd/mm` ou `dd/mm/aaaa`; datas futuras são
recusadas. Valores: vírgula decimal (`10,50`), ponto só como milhar (`1.000`).

### Voz

Envie um áudio, por exemplo "débito de dez reais e cinquenta no mercado". O bot
transcreve (faster-whisper local por padrão; `STT_PROVEDOR=openai` como alternativa),
normaliza os números falados, interpreta com o mesmo parser do texto e **sempre pede
confirmação** por botão antes de registrar. Campos ausentes ou ambíguos ("dez parcelas
de cem") viram pergunta — nada é inventado. O modelo é baixado na primeira transcrição
(`STT_MODELO_WHISPER`, padrão `small`, ~1–2 GB de RAM).

## Aplicação web

```bash
uv run uvicorn financas.web.app:criar_app --factory --reload   # http://127.0.0.1:8000
```

Entre com o login criado por `financas criar-admin`. Telas:

- **Dashboard:** receitas, despesas à vista, parcelas do mês, saldo, orçamento diário,
  compras no crédito do mês (informativo), comparação com o mês anterior, evolução e
  saldo acumulado do ano, distribuição por categoria/descrição e compromissos futuros.
  Filtros por mês e membro. Os números vêm das mesmas funções usadas pelo bot.
- **Lançamentos:** busca e filtros (tipo, membro, categoria, período, valor; situação
  para admin), criação, detalhe com parcelas vigentes e revisões substituídas, edição
  (com controle de concorrência), cancelamento com motivo, restauração (admin) e
  histórico de auditoria (admin).
- **Membros** e **Categorias:** somente administradores.

Segurança: sessões no banco (o cookie `HttpOnly`/`SameSite=Lax`, `Secure` em
produção, guarda um token cujo hash fica no banco), CSRF por sessão em todo POST,
templates com escape automático, CSP sem scripts inline, limite de 5 falhas por login
e 20 por IP a cada 15 minutos. Permissões são verificadas nos serviços, não só na tela.

## Estrutura

```
src/financas/
├── domain/          # dinheiro, calendário, parcelamento, orçamento (sem I/O)
├── db/              # modelos ORM e sessão
├── services/        # lançamentos, relatórios, autorização, auditoria, confirmações…
├── interpretation/  # parser de texto, números falados, interpretador de voz
├── integrations/    # transcrição (faster-whisper, OpenAI) e LLM (interface)
├── bot/             # processador (independente do Telegram), handlers, formatação
├── web/             # FastAPI: auth, rotas, templates Jinja2, static (CSS/JS)
└── config.py · logging.py · clock.py · cli.py
docs/                # regras de negócio e ADRs
```

Documentação das regras: [`docs/regras-de-negocio.md`](docs/regras-de-negocio.md).
