# Tio Patinhas — Gerenciador Financeiro Familiar

Bot do Telegram + aplicação web para a família registrar receitas, despesas e compras
parceladas, acompanhar o orçamento do mês e consultar compromissos futuros.

> **Estado:** Etapa 2 (fundação) concluída. Bot e web em desenvolvimento — veja
> `PROGRESSO.md`. Este README será completado nas próximas etapas.

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

## Estrutura

```
src/financas/
├── domain/          # dinheiro, calendário, parcelamento, orçamento (sem I/O)
├── db/              # modelos ORM e sessão
├── services/        # regras com banco (senhas, integridade; demais nas etapas 3–4)
├── interpretation/  # parser de texto e voz (etapa 3)
├── integrations/    # transcrição e LLM (etapas 3 e 5)
├── bot/ · web/      # interfaces (etapas 3 e 4)
└── config.py · logging.py · clock.py · cli.py
docs/                # regras de negócio e ADRs
```

Documentação das regras: [`docs/regras-de-negocio.md`](docs/regras-de-negocio.md).
