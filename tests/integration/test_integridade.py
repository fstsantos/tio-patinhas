"""``verificar-integridade`` detecta estados que os triggers impediriam."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from financas.services.integridade import verificar_integridade

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("limpar_banco")]


async def test_banco_vazio_sem_problemas(fabrica: async_sessionmaker[AsyncSession]) -> None:
    async with fabrica() as s:
        assert await verificar_integridade(s) == []


async def test_detecta_inconsistencias(fabrica: async_sessionmaker[AsyncSession]) -> None:
    async with fabrica() as s, s.begin():
        # Desliga triggers apenas nesta transação para simular dados corrompidos.
        await s.execute(text("SET LOCAL session_replication_role = replica"))
        m = (
            await s.execute(
                text("INSERT INTO membros (nome, papel) VALUES ('Ana','membro') RETURNING id")
            )
        ).scalar_one()
        base = (
            "INSERT INTO lancamentos (tipo, valor_total, descricao, descricao_normalizada,"
            " membro_id, criado_por, data_operacao, qtd_parcelas, status, cancelado_em, origem)"
            " VALUES (:tipo, 100, 'x', 'x', :m, :m, :d, :q, :st, :ce, 'WEB') RETURNING id"
        )
        soma_errada = (
            await s.execute(
                text(base),
                {
                    "tipo": "CREDITO",
                    "m": m,
                    "d": date(2026, 3, 1),
                    "q": 1,
                    "st": "ATIVO",
                    "ce": None,
                },
            )
        ).scalar_one()
        cancelado = (
            await s.execute(
                text(base),
                {
                    "tipo": "CREDITO",
                    "m": m,
                    "d": date(2026, 3, 1),
                    "q": 1,
                    "st": "CANCELADO",
                    "ce": date(2026, 3, 2),
                },
            )
        ).scalar_one()
        parcela = (
            "INSERT INTO parcelas (lancamento_id, revisao_lancamento, numero, valor, competencia,"
            " status) VALUES (:l, 1, 1, :v, '2026-04-01', 'PENDENTE')"
        )
        await s.execute(text(parcela), {"l": soma_errada, "v": Decimal("99.99")})
        await s.execute(text(parcela), {"l": cancelado, "v": Decimal("100.00")})
        await s.execute(text(parcela), {"l": 424242, "v": Decimal("1.00")})

    async with fabrica() as s:
        problemas = await verificar_integridade(s)
    regras = {(p.regra, p.entidade_id) for p in problemas}
    assert ("soma das parcelas vigentes difere do valor total", soma_errada) in regras
    assert ("lançamento cancelado com parcela pendente", cancelado) in regras
    assert any(p.regra == "parcela órfã" for p in problemas)
