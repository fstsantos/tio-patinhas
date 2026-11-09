"""Confirmações: autor, expiração, execução única sob concorrência; idempotência."""

from __future__ import annotations

import asyncio
from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from financas.db.models import TipoConfirmacao
from financas.services import confirmacoes, idempotencia
from financas.services.confirmacoes import Desfecho
from tests import factories as f

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("limpar_banco")]
HOJE = date(2026, 3, 20)


async def _nova(fabrica: async_sessionmaker[AsyncSession]):  # type: ignore[no-untyped-def]
    clock = f.relogio(HOJE)
    async with fabrica() as s, s.begin():
        ana = await f.membro(s, "Ana")
        bia = await f.membro(s, "Bia")
        conf = await confirmacoes.criar(
            s,
            clock,
            tipo=TipoConfirmacao.LANCAMENTO,
            membro_id=ana.id,
            chat_id=10,
            message_id=1,
            payload={"x": 1},
        )
    return clock, ana, bia, conf


async def test_somente_autor_e_uma_vez(fabrica: async_sessionmaker[AsyncSession]) -> None:
    clock, ana, bia, conf = await _nova(fabrica)
    async with fabrica() as s, s.begin():
        r = await confirmacoes.resolver(
            s, clock, conf.id, membro_id=bia.id, chat_id=10, confirmar=True
        )
        assert r.desfecho is Desfecho.OUTRO_MEMBRO
        r = await confirmacoes.resolver(
            s, clock, conf.id, membro_id=ana.id, chat_id=99, confirmar=True
        )
        assert r.desfecho is Desfecho.NAO_ENCONTRADA
        r = await confirmacoes.resolver(
            s, clock, conf.id, membro_id=ana.id, chat_id=10, confirmar=True
        )
        assert r.desfecho is Desfecho.OK and r.confirmacao is not None
        assert r.confirmacao.status == "EXECUTADA"
        r = await confirmacoes.resolver(
            s, clock, conf.id, membro_id=ana.id, chat_id=10, confirmar=False
        )
        assert r.desfecho is Desfecho.JA_RESOLVIDA


async def test_expira_em_cinco_minutos(fabrica: async_sessionmaker[AsyncSession]) -> None:
    clock, ana, _bia, conf = await _nova(fabrica)
    clock.avancar(minutes=5)
    async with fabrica() as s, s.begin():
        r = await confirmacoes.resolver(
            s, clock, conf.id, membro_id=ana.id, chat_id=10, confirmar=True
        )
        assert r.desfecho is Desfecho.EXPIRADA
    async with fabrica() as s, s.begin():
        r = await confirmacoes.resolver(
            s, clock, conf.id, membro_id=ana.id, chat_id=10, confirmar=True
        )
        assert r.desfecho is Desfecho.EXPIRADA


async def test_cancelar_antes_de_executar(fabrica: async_sessionmaker[AsyncSession]) -> None:
    clock, ana, _bia, conf = await _nova(fabrica)
    async with fabrica() as s, s.begin():
        r = await confirmacoes.resolver(
            s, clock, conf.id, membro_id=ana.id, chat_id=10, confirmar=False
        )
        assert r.desfecho is Desfecho.OK and r.confirmacao is not None
        assert r.confirmacao.status == "CANCELADA"


async def test_concorrencia_executa_uma_unica_vez(
    fabrica: async_sessionmaker[AsyncSession],
) -> None:
    clock, ana, _bia, conf = await _nova(fabrica)
    inicio = asyncio.Event()

    async def tentar() -> Desfecho:
        async with fabrica() as s, s.begin():
            await inicio.wait()
            r = await confirmacoes.resolver(
                s, clock, conf.id, membro_id=ana.id, chat_id=10, confirmar=True
            )
            await asyncio.sleep(0.05)  # segura o lock até o commit
            return r.desfecho

    tarefas = [asyncio.create_task(tentar()) for _ in range(5)]
    await asyncio.sleep(0.05)
    inicio.set()
    desfechos = await asyncio.gather(*tarefas)
    assert desfechos.count(Desfecho.OK) == 1
    assert desfechos.count(Desfecho.JA_RESOLVIDA) == 4


async def test_expirar_vencidas(fabrica: async_sessionmaker[AsyncSession]) -> None:
    clock, _ana, _bia, _conf = await _nova(fabrica)
    clock.avancar(minutes=6)
    async with fabrica() as s, s.begin():
        assert await confirmacoes.expirar_vencidas(s, clock) == 1


async def test_idempotencia_unicidade(fabrica: async_sessionmaker[AsyncSession]) -> None:
    clock = f.relogio(HOJE)
    async with fabrica() as s, s.begin():
        await idempotencia.registrar(
            s, clock, chat_id=1, message_id=2, update_id=3, resultado={"textos": ["ok"]}
        )
    async with fabrica() as s:
        assert await idempotencia.buscar(s, 1, 2) == {"textos": ["ok"]}
        assert await idempotencia.buscar(s, 1, 3) is None
    with pytest.raises(IntegrityError):
        async with fabrica() as s, s.begin():
            await idempotencia.registrar(
                s, clock, chat_id=1, message_id=2, update_id=4, resultado={}
            )
