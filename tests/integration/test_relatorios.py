"""Relatórios: saldo, parcelas do mês, compras do mês fora do saldo, sem dupla contagem."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from financas.db.models import Membro, Origem, TipoLancamento
from financas.domain.calendario import Mes, SituacaoMes
from financas.services import lancamentos as svc
from financas.services import relatorios
from financas.services.lancamentos import DadosLancamento
from tests import factories as f

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("limpar_banco")]

D = Decimal
HOJE = date(2026, 3, 20)
CREDITO, DEBITO, RECEITA = TipoLancamento.CREDITO, TipoLancamento.DEBITO, TipoLancamento.RECEITA


@pytest.fixture
async def cenario(fabrica: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    clock = f.relogio(HOJE)
    async with fabrica() as s, s.begin():
        ana = await f.membro(s, "Ana")
        bia = await f.membro(s, "Bia")
        mercado = await f.categoria(s, "Mercado")
        await f.lancar(s, clock, ana, RECEITA, "3500.00", "Salário", date(2026, 3, 5))
        await f.lancar(
            s, clock, ana, DEBITO, "100.00", "Mercado", date(2026, 3, 10), categoria_id=mercado.id
        )
        await f.lancar(s, clock, bia, DEBITO, "50.00", "Farmácia", date(2026, 3, 12))
        await f.lancar(s, clock, ana, DEBITO, "20.00", "padaria", date(2026, 2, 28))
        await f.lancar(s, clock, ana, CREDITO, "300.00", "geladeira", date(2026, 2, 10), 3)
        await f.lancar(s, clock, bia, CREDITO, "1000.00", "mercado livre", date(2026, 3, 15), 10)
        cancelada = await f.lancar(s, clock, ana, CREDITO, "200.00", "x", date(2026, 2, 1), 2)
        await svc.cancelar(s, clock, ana, cancelada.lancamento.id, origem=Origem.WEB)
        rep = await f.lancar(s, clock, ana, CREDITO, "120.00", "tênis", date(2026, 1, 5), 2)
        await svc.editar(
            s,
            clock,
            ana,
            rep.lancamento.id,
            DadosLancamento(CREDITO, D("120.00"), "tênis", date(2026, 1, 5), 3),
            origem=Origem.WEB,
            revisao_esperada=rep.lancamento.revisao,
        )
        return {"ana": ana.id, "bia": bia.id}


async def test_resumo_mensal(
    fabrica: async_sessionmaker[AsyncSession], cenario: dict[str, int]
) -> None:
    async with fabrica() as s:
        r = await relatorios.resumo_mensal(s, Mes(2026, 3), HOJE)
    o = r.orcamento
    assert (o.receitas, o.debitos, o.parcelas) == (D("3500.00"), D("150.00"), D("140.00"))
    assert o.saldo == D("3210.00")
    assert o.dias_restantes == 12
    assert o.orcamento_diario == D("267.50")
    assert [(p.descricao, p.numero, p.total_parcelas) for p in r.parcelas] == [
        ("tênis", 2, 3),
        ("geladeira", 1, 3),
    ]
    # compra no crédito do próprio mês: informativa, fora do saldo
    assert r.total_credito_contratado == D("1000.00")
    assert r.impacto_futuro[0] == (Mes(2026, 4), D("100.00"))
    assert len(r.impacto_futuro) == 10
    assert {g.nome: g.total for g in r.por_categoria} == {
        "Sem categoria": D("190.00"),
        "Mercado": D("100.00"),
    }
    assert {g.nome: g.total for g in r.por_descricao} == {
        "mercado": D("100.00"),
        "geladeira": D("100.00"),
        "farmacia": D("50.00"),
        "tenis": D("40.00"),
    }


async def test_filtro_por_membro(
    fabrica: async_sessionmaker[AsyncSession], cenario: dict[str, int]
) -> None:
    async with fabrica() as s:
        r = await relatorios.resumo_mensal(s, Mes(2026, 3), HOJE, membro_id=cenario["bia"])
    assert (r.orcamento.receitas, r.orcamento.debitos, r.orcamento.parcelas) == (
        D("0"),
        D("50.00"),
        D("0"),
    )
    assert r.orcamento.orcamento_diario == D("0.00")
    assert r.orcamento.deficit


async def test_mes_encerrado_e_futuro(
    fabrica: async_sessionmaker[AsyncSession], cenario: dict[str, int]
) -> None:
    async with fabrica() as s:
        fev = await relatorios.resumo_mensal(s, Mes(2026, 2), HOJE)
        abr = await relatorios.resumo_mensal(s, Mes(2026, 4), HOJE)
    assert fev.orcamento.situacao is SituacaoMes.ENCERRADO
    assert fev.orcamento.orcamento_diario is None
    # fev: débito 20 + parcela tênis 1/3 (40); a cancelada não conta
    assert (fev.orcamento.debitos, fev.orcamento.parcelas) == (D("20.00"), D("40.00"))
    assert abr.orcamento.situacao is SituacaoMes.FUTURO
    assert abr.orcamento.dias_restantes == 30
    assert abr.orcamento.parcelas == D("240.00")  # geladeira 100 + ML 100 + tênis 40


async def test_resumo_anual_coerente_com_mensal(
    fabrica: async_sessionmaker[AsyncSession], cenario: dict[str, int]
) -> None:
    async with fabrica() as s:
        anual = await relatorios.resumo_anual(s, 2026, HOJE)
        mensais = [await relatorios.resumo_mensal(s, Mes(2026, m), HOJE) for m in range(1, 13)]
    assert len(anual.meses) == 12
    for a, m in zip(anual.meses, mensais, strict=True):
        assert a.orcamento == m.orcamento
    assert anual.meses[0].orcamento.saldo == D("0.00")
    acumulado = D("0")
    for a in anual.meses:
        acumulado += a.orcamento.saldo
        assert a.saldo_acumulado == acumulado
    assert anual.saldo == acumulado
    # 2026 contém só parcelas até dez: ML abr..dez (900), geladeira mar..mai (300), tênis 120
    assert anual.parcelas == D("1320.00")


async def test_resumo_credito(
    fabrica: async_sessionmaker[AsyncSession], cenario: dict[str, int]
) -> None:
    async with fabrica() as s:
        c = await relatorios.resumo_credito(s, HOJE)
    assert c.blocos[0].mes == Mes(2026, 3)
    assert c.blocos[0].subtotal == D("140.00")
    assert c.blocos[-1].mes == Mes(2027, 1)
    # pendentes de mar/2026 em diante: geladeira 300, ML 1000, tênis 80
    assert c.total == D("1380.00")
    p = c.blocos[0].parcelas[0]
    assert (p.descricao, p.membro, p.data_compra) == ("tênis", "Ana", date(2026, 1, 5))


async def test_cancelamento_reflete_imediatamente(
    fabrica: async_sessionmaker[AsyncSession], cenario: dict[str, int]
) -> None:
    clock = f.relogio(HOJE)
    async with fabrica() as s, s.begin():
        ana = await s.get(Membro, cenario["ana"])
        assert ana is not None
        geladeira = next(x for x in await svc.ultimos(s, ana.id) if x.descricao == "geladeira")
        await svc.cancelar(s, clock, ana, geladeira.id, origem=Origem.WEB)
    async with fabrica() as s:
        r = await relatorios.resumo_mensal(s, Mes(2026, 3), HOJE)
    assert r.orcamento.parcelas == D("40.00")
