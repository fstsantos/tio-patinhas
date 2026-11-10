"""Fixtures compartilhadas.

Banco de teste: ``TEST_DATABASE_URL`` (ex.: o serviço de ``compose.test.yaml``) ou,
na ausência, um PostgreSQL 16 embutido e descartável (pacote ``pgserver``).
Nunca aponte ``TEST_DATABASE_URL`` para o banco real: o esquema é apagado.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from financas.db.session import criar_engine, criar_fabrica, normalizar_url

RAIZ = Path(__file__).resolve().parent.parent


def alembic_config(url: str) -> Config:
    cfg = Config(str(RAIZ / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", normalizar_url(url))
    cfg.attributes["configurar_logs"] = False
    return cfg


@pytest.fixture(autouse=True)
def _sem_provedores_reais(monkeypatch: pytest.MonkeyPatch) -> None:
    """Os testes nunca usam a LLM do ``.env`` local: nada de chamada real nem de custo.

    Variável de ambiente vence o ``.env``; quem precisa de LLM injeta uma falsa.
    """
    monkeypatch.setenv("LLM_PROVEDOR", "nenhum")
    for chave in ("LLM_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(chave, "")


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        yield url
        return
    import pgserver

    diretorio = tempfile.mkdtemp(prefix="financas-pg-")
    servidor = pgserver.get_server(diretorio, cleanup_mode="delete")
    servidor.psql("CREATE DATABASE financas_teste;")
    yield servidor.get_uri("financas_teste")
    servidor.cleanup()


@pytest.fixture(scope="session")
def banco_migrado(database_url: str) -> str:
    cfg = alembic_config(database_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    return database_url


@pytest.fixture(scope="session")
async def engine(banco_migrado: str) -> AsyncIterator[AsyncEngine]:
    eng = criar_engine(banco_migrado)
    yield eng
    await eng.dispose()


@pytest.fixture(scope="session")
def fabrica(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return criar_fabrica(engine)


TABELAS = (
    "tentativas_login, sessoes_web, confirmacoes_pendentes, updates_processados, "
    "auditoria, parcelas, lancamentos, categorias, membros"
)


@pytest.fixture
async def limpar_banco(engine: AsyncEngine) -> AsyncIterator[None]:
    """Isolamento entre testes: TRUNCATE (não dispara o trigger de auditoria)."""
    from sqlalchemy import text

    async with engine.begin() as con:
        await con.execute(text(f"TRUNCATE {TABELAS} RESTART IDENTITY CASCADE"))
    yield
