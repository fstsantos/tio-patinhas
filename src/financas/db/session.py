"""Engine e fábrica de sessões assíncronas (SQLAlchemy 2 + psycopg 3)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def normalizar_url(url: str) -> str:
    """Força o driver psycopg 3 (``postgresql://`` → ``postgresql+psycopg://``)."""
    for prefixo in ("postgresql://", "postgres://"):
        if url.startswith(prefixo):
            return "postgresql+psycopg://" + url[len(prefixo) :]
    return url


def criar_engine(url: str, *, echo: bool = False) -> AsyncEngine:
    return create_async_engine(normalizar_url(url), echo=echo, pool_pre_ping=True)


def criar_fabrica(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def transacao(fabrica: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    """Abre uma sessão com transação: commit ao sair, rollback em exceção."""
    async with fabrica() as sessao, sessao.begin():
        yield sessao
