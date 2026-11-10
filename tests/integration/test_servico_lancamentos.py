"""Serviço de lançamentos: criação, permissões, cancelamento, edição e restauração."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from financas.db.models import Auditoria, Lancamento, Origem, Parcela, TipoLancamento
from financas.domain.erros import (
    ConflitoConcorrencia,
    DataInvalida,
    NaoEncontrado,
    OperacaoInvalida,
    ParcelasInvalidas,
    SemPermissao,
)
from financas.services import lancamentos as svc
from financas.services.integridade import verificar_integridade
from financas.services.lancamentos import DadosLancamento
from tests import factories as f

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("limpar_banco")]

D = Decimal
HOJE = date(2026, 3, 20)
CREDITO, DEBITO, RECEITA = TipoLancamento.CREDITO, TipoLancamento.DEBITO, TipoLancamento.RECEITA


@pytest.fixture
def clock():  # type: ignore[no-untyped-def]
    return f.relogio(HOJE)


async def _acoes(s: AsyncSession, lanc_id: int) -> list[str]:
    r = await s.execute(
        select(Auditoria.acao).where(Auditoria.entidade_id == lanc_id).order_by(Auditoria.id)
    )
    return list(r.scalars())


async def test_credito_gera_parcelas_e_auditoria(
    fabrica: async_sessionmaker[AsyncSession], clock
) -> None:  # type: ignore[no-untyped-def]
    async with fabrica() as s, s.begin():
        ana = await f.membro(s)
        r = await f.lancar(s, clock, ana, CREDITO, "1000.00", "mercado livre", parcelas=10)
    assert r.lancamento.criado_por == r.lancamento.membro_id == ana.id
    assert [p.competencia for p in r.parcelas][:2] == [date(2026, 4, 1), date(2026, 5, 1)]
    assert r.parcelas[-1].competencia == date(2027, 1, 1)
    async with fabrica() as s:
        assert await _acoes(s, r.lancamento.id) == ["CRIACAO"]
        assert await verificar_integridade(s) == []


@pytest.mark.parametrize("tipo", [DEBITO, RECEITA, CREDITO])
async def test_data_futura_permitida(
    fabrica: async_sessionmaker[AsyncSession],
    clock,  # type: ignore[no-untyped-def]
    tipo: TipoLancamento,
) -> None:
    async with fabrica() as s, s.begin():
        ana = await f.membro(s)
        futuro = HOJE + timedelta(days=40)  # 29/04/2026
        r = await f.lancar(
            s, clock, ana, tipo, "10.00", data=futuro, parcelas=2 if tipo is CREDITO else None
        )
        assert r.lancamento.data_operacao == futuro
        # No crédito, as parcelas seguem a regra de sempre a partir da data da compra.
        assert [p.competencia for p in r.parcelas] == (
            [date(2026, 5, 1), date(2026, 6, 1)] if tipo is CREDITO else []
        )
    async with fabrica() as s:
        assert await verificar_integridade(s) == []


async def test_retroativo_permitido(fabrica: async_sessionmaker[AsyncSession], clock) -> None:  # type: ignore[no-untyped-def]
    async with fabrica() as s, s.begin():
        ana = await f.membro(s)
        r = await f.lancar(s, clock, ana, CREDITO, "300.00", data=date(2026, 2, 10), parcelas=3)
    assert r.parcelas[0].competencia == date(2026, 3, 1)


async def test_validacoes_de_parcelas(fabrica: async_sessionmaker[AsyncSession], clock) -> None:  # type: ignore[no-untyped-def]
    async with fabrica() as s, s.begin():
        ana = await f.membro(s)
        with pytest.raises(ParcelasInvalidas):
            await f.lancar(s, clock, ana, CREDITO, "10.00", parcelas=None)
        with pytest.raises(ParcelasInvalidas):
            await f.lancar(s, clock, ana, CREDITO, "10.00", parcelas=49)
        with pytest.raises(ParcelasInvalidas):
            await f.lancar(s, clock, ana, DEBITO, "10.00", parcelas=2)


async def test_permissoes_de_criacao(fabrica: async_sessionmaker[AsyncSession], clock) -> None:  # type: ignore[no-untyped-def]
    dados = DadosLancamento(DEBITO, D("10.00"), "x", HOJE)
    async with fabrica() as s, s.begin():
        ana = await f.membro(s, "Ana")
        bia = await f.membro(s, "Bia")
        adm = await f.membro(s, "Adm", admin=True)
        inativo = await f.membro(s, "Ina", ativo=False)
        with pytest.raises(SemPermissao):
            await svc.criar(s, clock, ana, dados, origem=Origem.WEB, titular_id=bia.id)
        with pytest.raises(SemPermissao):
            await svc.criar(s, clock, inativo, dados, origem=Origem.WEB)
        with pytest.raises(NaoEncontrado):
            await svc.criar(s, clock, adm, dados, origem=Origem.WEB, titular_id=inativo.id)
        r = await svc.criar(s, clock, adm, dados, origem=Origem.WEB, titular_id=bia.id)
    assert (r.lancamento.membro_id, r.lancamento.criado_por) == (bia.id, adm.id)


async def test_categoria_incompativel(fabrica: async_sessionmaker[AsyncSession], clock) -> None:  # type: ignore[no-untyped-def]
    async with fabrica() as s, s.begin():
        ana = await f.membro(s)
        salario = await f.categoria(s, "Salário", "RECEITA")
        mercado = await f.categoria(s, "Mercado", "DESPESA")
        with pytest.raises(OperacaoInvalida):
            await f.lancar(s, clock, ana, DEBITO, "10.00", categoria_id=salario.id)
        await f.lancar(s, clock, ana, DEBITO, "10.00", categoria_id=mercado.id)
        with pytest.raises(NaoEncontrado):
            await f.lancar(s, clock, ana, DEBITO, "10.00", categoria_id=9999)


async def test_cancelar_compra_cancela_parcelas(
    fabrica: async_sessionmaker[AsyncSession], clock
) -> None:  # type: ignore[no-untyped-def]
    async with fabrica() as s, s.begin():
        ana = await f.membro(s)
        r = await f.lancar(s, clock, ana, CREDITO, "100.00", parcelas=3)
    async with fabrica() as s, s.begin():
        ana_ = await s.get(type(ana), ana.id)
        assert ana_ is not None
        lanc = await svc.cancelar(
            s, clock, ana_, r.lancamento.id, origem=Origem.WEB, motivo="duplicado"
        )
        assert lanc.motivo_cancelamento == "duplicado"
    async with fabrica() as s:
        status = (await s.execute(select(Parcela.status))).scalars().all()
        assert set(status) == {"CANCELADA"}
        assert await _acoes(s, r.lancamento.id) == ["CRIACAO", "CANCELAMENTO"]
        assert await verificar_integridade(s) == []
    async with fabrica() as s, s.begin():
        with pytest.raises(OperacaoInvalida):
            await svc.cancelar(s, clock, ana_, r.lancamento.id, origem=Origem.WEB)


async def test_matriz_cancelamento(fabrica: async_sessionmaker[AsyncSession], clock) -> None:  # type: ignore[no-untyped-def]
    async with fabrica() as s, s.begin():
        ana = await f.membro(s, "Ana")
        bia = await f.membro(s, "Bia")
        adm = await f.membro(s, "Adm", admin=True)
        r1 = await f.lancar(s, clock, ana, DEBITO, "10.00")
        r2 = await f.lancar(s, clock, ana, DEBITO, "20.00")
        with pytest.raises(SemPermissao):
            await svc.cancelar(s, clock, bia, r1.lancamento.id, origem=Origem.TELEGRAM_TEXTO)
        await svc.cancelar(s, clock, adm, r1.lancamento.id, origem=Origem.WEB)
        await svc.cancelar(s, clock, ana, r2.lancamento.id, origem=Origem.TELEGRAM_TEXTO)
        with pytest.raises(NaoEncontrado):
            await svc.cancelar(s, clock, ana, 424242, origem=Origem.TELEGRAM_TEXTO)


async def test_editar_descricao_nao_reparcela(
    fabrica: async_sessionmaker[AsyncSession], clock
) -> None:  # type: ignore[no-untyped-def]
    async with fabrica() as s, s.begin():
        ana = await f.membro(s)
        r = await f.lancar(s, clock, ana, CREDITO, "100.00", "tv", parcelas=2)
        rev = r.lancamento.revisao
        dados = DadosLancamento(CREDITO, D("100.00"), "TV da sala", r.lancamento.data_operacao, 2)
        e = await svc.editar(
            s, clock, ana, r.lancamento.id, dados, origem=Origem.WEB, revisao_esperada=rev
        )
        assert e.lancamento.descricao_normalizada == "tv da sala"
        assert e.lancamento.revisao == rev + 1
        assert {p.revisao_lancamento for p in e.parcelas} == {rev}
    async with fabrica() as s:
        assert len((await s.execute(select(Parcela))).scalars().all()) == 2


async def test_reparcelar_substitui_parcelas(
    fabrica: async_sessionmaker[AsyncSession], clock
) -> None:  # type: ignore[no-untyped-def]
    async with fabrica() as s, s.begin():
        ana = await f.membro(s)
        r = await f.lancar(s, clock, ana, CREDITO, "100.00", "tv", parcelas=2)
        dados = DadosLancamento(CREDITO, D("90.00"), "tv", date(2026, 1, 15), 3)
        e = await svc.editar(
            s,
            clock,
            ana,
            r.lancamento.id,
            dados,
            origem=Origem.WEB,
            revisao_esperada=r.lancamento.revisao,
            motivo="valor errado",
        )
        assert [p.valor for p in e.parcelas] == [D("30.00")] * 3
        assert e.parcelas[0].competencia == date(2026, 2, 1)
    async with fabrica() as s:
        todas = await svc.todas_parcelas(s, r.lancamento.id)
        assert sorted(p.status for p in todas) == ["PENDENTE"] * 3 + ["SUBSTITUIDA"] * 2
        assert await verificar_integridade(s) == []
        assert await _acoes(s, r.lancamento.id) == ["CRIACAO", "EDICAO"]


async def test_conflito_de_concorrencia(fabrica: async_sessionmaker[AsyncSession], clock) -> None:  # type: ignore[no-untyped-def]
    async with fabrica() as s, s.begin():
        ana = await f.membro(s)
        r = await f.lancar(s, clock, ana, DEBITO, "10.00", "a")
    rev = r.lancamento.revisao
    dados = DadosLancamento(DEBITO, D("10.00"), "b", HOJE)
    async with fabrica() as s, s.begin():
        await svc.editar(
            s, clock, ana, r.lancamento.id, dados, origem=Origem.WEB, revisao_esperada=rev
        )
    async with fabrica() as s, s.begin():
        with pytest.raises(ConflitoConcorrencia):
            await svc.editar(
                s,
                clock,
                ana,
                r.lancamento.id,
                replace(dados, descricao="c"),
                origem=Origem.WEB,
                revisao_esperada=rev,
            )


async def test_editar_permissoes_e_estado(fabrica: async_sessionmaker[AsyncSession], clock) -> None:  # type: ignore[no-untyped-def]
    async with fabrica() as s, s.begin():
        ana = await f.membro(s)
        bia = await f.membro(s, "Bia")
        r = await f.lancar(s, clock, ana, DEBITO, "10.00")
        dados = DadosLancamento(DEBITO, D("11.00"), "x", HOJE)
        with pytest.raises(SemPermissao):
            await svc.editar(
                s,
                clock,
                bia,
                r.lancamento.id,
                dados,
                origem=Origem.WEB,
                revisao_esperada=r.lancamento.revisao,
            )
        with pytest.raises(OperacaoInvalida):
            await svc.editar(
                s,
                clock,
                ana,
                r.lancamento.id,
                replace(dados, tipo=RECEITA),
                origem=Origem.WEB,
                revisao_esperada=r.lancamento.revisao,
            )
        await svc.cancelar(s, clock, ana, r.lancamento.id, origem=Origem.WEB)
        with pytest.raises(OperacaoInvalida):
            await svc.editar(
                s,
                clock,
                ana,
                r.lancamento.id,
                dados,
                origem=Origem.WEB,
                revisao_esperada=r.lancamento.revisao,
            )


async def test_editar_data_retroativa_apos_criacao(
    fabrica: async_sessionmaker[AsyncSession],
) -> None:
    """Criado em 10/03 com data 10/03; editado em 20/03 para 15/03 (depois da criação)."""
    c1 = f.relogio(date(2026, 3, 10))
    async with fabrica() as s, s.begin():
        ana = await f.membro(s)
        r = await f.lancar(s, c1, ana, DEBITO, "10.00")
    c2 = f.relogio(date(2026, 3, 20))
    async with fabrica() as s, s.begin():
        await svc.editar(
            s,
            c2,
            ana,
            r.lancamento.id,
            DadosLancamento(DEBITO, D("10.00"), "teste", date(2026, 3, 15)),
            origem=Origem.WEB,
            revisao_esperada=r.lancamento.revisao,
        )


async def test_restaurar(fabrica: async_sessionmaker[AsyncSession], clock) -> None:  # type: ignore[no-untyped-def]
    async with fabrica() as s, s.begin():
        ana = await f.membro(s)
        adm = await f.membro(s, "Adm", admin=True)
        r = await f.lancar(s, clock, ana, CREDITO, "100.00", parcelas=2)
        await svc.editar(
            s,
            clock,
            ana,
            r.lancamento.id,
            DadosLancamento(CREDITO, D("100.00"), "teste", HOJE, 4),
            origem=Origem.WEB,
            revisao_esperada=r.lancamento.revisao,
        )
        await svc.cancelar(s, clock, ana, r.lancamento.id, origem=Origem.WEB)
        with pytest.raises(SemPermissao):
            await svc.restaurar(s, clock, ana, r.lancamento.id, origem=Origem.WEB, motivo="x")
        with pytest.raises(OperacaoInvalida, match="motivo"):
            await svc.restaurar(s, clock, adm, r.lancamento.id, origem=Origem.WEB, motivo=" ")
        res = await svc.restaurar(
            s, clock, adm, r.lancamento.id, origem=Origem.WEB, motivo="cancelado por engano"
        )
        assert len(res.parcelas) == 4 and {p.status for p in res.parcelas} == {"PENDENTE"}
        assert res.lancamento.cancelado_em is None
        with pytest.raises(OperacaoInvalida):
            await svc.restaurar(s, clock, adm, r.lancamento.id, origem=Origem.WEB, motivo="x")
    async with fabrica() as s:
        assert await verificar_integridade(s) == []
        assert await _acoes(s, r.lancamento.id) == [
            "CRIACAO",
            "EDICAO",
            "CANCELAMENTO",
            "RESTAURACAO",
        ]


async def test_ultimos_e_desfazer(fabrica: async_sessionmaker[AsyncSession], clock) -> None:  # type: ignore[no-untyped-def]
    async with fabrica() as s, s.begin():
        ana = await f.membro(s)
        bia = await f.membro(s, "Bia")
        for i in range(12):
            clock.avancar(minutes=1)
            await f.lancar(s, clock, ana, DEBITO, f"{i + 1}.00", f"item {i}")
        await f.lancar(s, clock, bia, DEBITO, "99.00")
        ult = await svc.ultimos(s, ana.id)
        assert [lanc.descricao for lanc in ult] == [f"item {i}" for i in range(11, 1, -1)]
        d = await svc.ultimo_desfazivel(s, clock, ana)
        assert d is not None and d.descricao == "item 11"
        clock.avancar(minutes=10, seconds=1)
        assert await svc.ultimo_desfazivel(s, clock, ana) is None
        assert len((await s.execute(select(Lancamento))).scalars().all()) == 13


async def test_credito_com_primeira_competencia(
    fabrica: async_sessionmaker[AsyncSession], clock
) -> None:  # type: ignore[no-untyped-def]
    """Compra em 03/03 que a fatura já cobra em março (antes do fechamento)."""
    dados = DadosLancamento(
        CREDITO, D("300.00"), "lojinha", date(2026, 3, 3), 3, primeira_competencia=date(2026, 3, 1)
    )
    async with fabrica() as s, s.begin():
        ana = await f.membro(s)
        r = await svc.criar(s, clock, ana, dados, origem=Origem.CLI)
    assert r.lancamento.primeira_competencia == date(2026, 3, 1)
    assert [p.competencia for p in r.parcelas] == [
        date(2026, 3, 1),
        date(2026, 4, 1),
        date(2026, 5, 1),
    ]
    async with fabrica() as s:
        assert await verificar_integridade(s) == []
        reg = (
            await s.execute(select(Auditoria).where(Auditoria.entidade_id == r.lancamento.id))
        ).scalar_one()
        assert reg.dados_depois is not None
        assert reg.dados_depois["primeira_competencia"] == "2026-03-01"


@pytest.mark.parametrize(
    ("tipo", "qtd", "primeira", "erro"),
    [
        (CREDITO, 2, date(2026, 2, 1), DataInvalida),  # antes do mês da compra
        (CREDITO, 2, date(2026, 6, 1), DataInvalida),  # depois de M + 2
        (DEBITO, None, date(2026, 3, 1), ParcelasInvalidas),  # só crédito
    ],
)
async def test_primeira_competencia_invalida(
    fabrica: async_sessionmaker[AsyncSession],
    clock,  # type: ignore[no-untyped-def]
    tipo: TipoLancamento,
    qtd: int | None,
    primeira: date,
    erro: type[Exception],
) -> None:
    dados = DadosLancamento(tipo, D("100.00"), "x", date(2026, 3, 10), qtd, None, primeira)
    async with fabrica() as s, s.begin():
        ana = await f.membro(s)
        with pytest.raises(erro):
            await svc.criar(s, clock, ana, dados, origem=Origem.WEB)


async def test_editar_primeira_competencia_reparcela(
    fabrica: async_sessionmaker[AsyncSession], clock
) -> None:  # type: ignore[no-untyped-def]
    async with fabrica() as s, s.begin():
        ana = await f.membro(s)
        r = await f.lancar(
            s, clock, ana, CREDITO, "100.00", "tv", data=date(2026, 3, 2), parcelas=2
        )
        assert r.parcelas[0].competencia == date(2026, 4, 1)
        base = DadosLancamento(CREDITO, D("100.00"), "tv", date(2026, 3, 2), 2)
        # informar o mês da 1ª parcela reparcela
        e = await svc.editar(
            s,
            clock,
            ana,
            r.lancamento.id,
            replace(base, primeira_competencia=date(2026, 3, 1)),
            origem=Origem.WEB,
            revisao_esperada=r.lancamento.revisao,
        )
        assert [p.competencia for p in e.parcelas] == [date(2026, 3, 1), date(2026, 4, 1)]
        rev_parcelas = e.lancamento.revisao
        # mudar só a descrição mantém o mês informado e não reparcela
        e2 = await svc.editar(
            s,
            clock,
            ana,
            r.lancamento.id,
            replace(base, descricao="TV", primeira_competencia=date(2026, 3, 1)),
            origem=Origem.WEB,
            revisao_esperada=rev_parcelas,
        )
        assert {p.revisao_lancamento for p in e2.parcelas} == {rev_parcelas}
        # voltar à regra padrão (campo vazio) reparcela de novo
        e3 = await svc.editar(
            s,
            clock,
            ana,
            r.lancamento.id,
            replace(base, descricao="TV"),
            origem=Origem.WEB,
            revisao_esperada=rev_parcelas + 1,
        )
        assert e3.lancamento.primeira_competencia is None
        assert [p.competencia for p in e3.parcelas] == [date(2026, 4, 1), date(2026, 5, 1)]
    async with fabrica() as s:
        assert await verificar_integridade(s) == []
