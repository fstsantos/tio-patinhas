# ADR 0001 — PostgreSQL real nos testes, embutido quando não há Docker

- **Situação:** aceita (Etapa 2)
- **Contexto:** a spec exige testes de integração com PostgreSQL real e isolado. A
  máquina de desenvolvimento inicial não tem Docker nem PostgreSQL instalados.
- **Decisão:** a fixture `database_url` usa `TEST_DATABASE_URL` quando definido
  (ex.: `compose.test.yaml`, CI) e, na ausência, inicia um PostgreSQL 16 descartável
  via pacote `pgserver` (dependência apenas de desenvolvimento). SQLite não é usado.
- **Consequências:** `uv run pytest` funciona sem infraestrutura extra; o mesmo
  esquema (migrações Alembic, triggers) é exercitado em ambos os casos.
