# Atalhos de desenvolvimento. Requer uv (https://docs.astral.sh/uv/).
# DOCKER=1 faz migrate, criar-admin, integridade, backup e restore usarem a stack
# do compose.yaml (ex.: make criar-admin DOCKER=1 ARGS="--telegram-id 123").
UV ?= uv
COMPOSE ?= docker compose
DOCKER ?= 0
ARGS ?=

ifeq ($(DOCKER),1)
  # Garante o banco no ar e roda o comando num contêiner descartável da imagem.
  RUN = $(COMPOSE) up -d --wait db && $(COMPOSE) run --rm --no-deps web
else
  RUN = $(UV) run
endif

.PHONY: help install lint format typecheck test test-unit test-integration cov check \
        up down logs migrate migrate-down criar-admin integridade test-docker \
        web bot backup restore

help:  ## Lista os alvos
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-18s %s\n", $$1, $$2}'

install:  ## Instala dependências travadas (dev + voz + llm)
	$(UV) sync --frozen --extra voz --extra llm

lint:  ## ruff (lint + formatação)
	$(UV) run ruff check src tests migrations
	$(UV) run ruff format --check src tests migrations

format:  ## Formata o código
	$(UV) run ruff format src tests migrations
	$(UV) run ruff check --fix src tests migrations

typecheck:  ## mypy estrito
	$(UV) run mypy

test:  ## Todos os testes (PostgreSQL embutido ou TEST_DATABASE_URL)
	$(UV) run pytest

test-unit:  ## Somente testes unitários
	$(UV) run pytest tests/unit

test-integration:  ## Somente testes de integração
	$(UV) run pytest tests/integration

cov:  ## Testes com cobertura
	$(UV) run pytest --cov --cov-report=term-missing

check: lint typecheck cov  ## Portão de qualidade: lint, tipos e testes com cobertura

up:  ## Sobe PostgreSQL, web e bot
	$(COMPOSE) up -d --build

down:  ## Derruba os serviços
	$(COMPOSE) down

logs:  ## Acompanha os logs
	$(COMPOSE) logs -f

migrate:  ## Aplica migrações (DATABASE_URL do .env; DOCKER=1 no Compose)
	$(RUN) alembic upgrade head

migrate-down:  ## Reverte a última migração
	$(RUN) alembic downgrade -1

criar-admin:  ## Cria um administrador (ARGS="--telegram-id <id>")
	$(RUN) financas criar-admin $(ARGS)

integridade:  ## Verifica integridade de compras e parcelas
	$(RUN) financas verificar-integridade

test-docker:  ## Testes contra o PostgreSQL isolado de compose.test.yaml
	$(COMPOSE) -f compose.test.yaml up -d --wait
	TEST_DATABASE_URL=postgresql+psycopg://teste:teste@localhost:55432/financas_teste \
		$(UV) run pytest; status=$$?; \
		$(COMPOSE) -f compose.test.yaml down -v; exit $$status

web:  ## Web local com recarga (http://127.0.0.1:8000)
	$(UV) run uvicorn financas.web.app:criar_app --factory --reload

bot:  ## Bot local (polling)
	$(UV) run python -m financas.bot.app

backup:  ## Backup do banco em ./backups (DOCKER=1 usa o contêiner db)
	$(if $(filter 1,$(DOCKER)),BACKUP_VIA_DOCKER=1 )./scripts/backup.sh

restore:  ## Restaura ARQUIVO=<backup.dump> (apaga os dados atuais!)
ifeq ($(DOCKER),1)
	$(COMPOSE) up -d --wait db
	$(COMPOSE) stop web bot
	RESTORE_VIA_DOCKER=1 ./scripts/restore.sh $(ARQUIVO) --sim
	$(RUN) financas verificar-integridade
	$(COMPOSE) start web bot
else
	./scripts/restore.sh $(ARQUIVO) --sim
	$(RUN) financas verificar-integridade
endif
