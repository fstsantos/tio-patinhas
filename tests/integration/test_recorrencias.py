"""Recorrências: geração idempotente, catch-up, edição, encerramento e permissões."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from financas.db.models import Auditoria, Lancamento, Membro, Origem, Parcela, TipoLancamento
from financas.domain.calendario import Mes
from financas.domain.erros import DataInvalida, OperacaoInvalida, SemPermissao
from financas.services import lancamentos as svc_lanc
from financas.services import recorrencias as svc
from financas.services import relatorios
from financas.services.recorrencias import DadosRecorrencia
from tests import factories as f

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("limpar_banco")]

HOJE = date(2026, 3, 10)


def dados(
    tipo: TipoLancamento = TipoLancamento.DEBITO,
    valor: str = "1500.00",
    descricao: str = "Aluguel",
    dia: int = 15,
    **kw: object,
) -> DadosRecorrencia:
    return DadosRecorrencia(tipo, Decimal(valor), descricao, dia, **kw)  # type: ignore[arg-type]


async def _lancamentos(s: AsyncSession) -> list[Lancamento]:
    return list((await s.execute(select(Lancamento).order_by(Lancamento.data_operacao))).scalars())


async def test_criar_gera_o_mes_corrente_com_data_futura_e_conta_no_saldo(
    fabrica: async_sessionmaker[AsyncSession],
) -> None:
    clock = f.relogio(HOJE)
    async with fabrica() as s, s.begin():
        ana = await f.membro(s)
        await svc.criar(s, clock, ana, dados(TipoLancamento.RECEITA, "5000.00", "Salário", 30))
        rec = await svc.criar(s, clock, ana, dados())
        lancs = await _lancamentos(s)
        assert [(x.descricao, x.data_operacao) for x in lancs] == [
            ("Aluguel", date(2026, 3, 15)),
            ("Salário", date(2026, 3, 30)),
        ]
        aluguel = lancs[0]
        assert aluguel.origem == Origem.RECORRENCIA.value
        assert (aluguel.recorrencia_id, aluguel.recorrencia_mes) == (rec.id, date(2026, 3, 1))
        assert aluguel.membro_id == aluguel.criado_por == ana.id
        resumo = await relatorios.resumo_mensal(s, Mes(2026, 3), HOJE)
        assert resumo.orcamento.saldo == Decimal("3500.00")
        # Auditoria do molde e do lançamento gerado.
        entidades = set((await s.execute(select(Auditoria.entidade))).scalars())
        assert entidades == {"recorrencia", "lancamento"}


async def test_credito_gera_compra_a_vista_que_cai_no_mes_seguinte(
    fabrica: async_sessionmaker[AsyncSession],
) -> None:
    async with fabrica() as s, s.begin():
        ana = await f.membro(s)
        rec = await svc.criar(
            s, f.relogio(HOJE), ana, dados(TipoLancamento.CREDITO, "39.90", "Filmes", 31)
        )
        await svc.gerar(s, f.relogio(date(2026, 4, 1)))
        lancs = await _lancamentos(s)
        assert [(x.tipo, x.qtd_parcelas, x.data_operacao) for x in lancs] == [
            ("CREDITO", 1, date(2026, 3, 31)),
            ("CREDITO", 1, date(2026, 4, 30)),
        ]
        assert {x.recorrencia_id for x in lancs} == {rec.id}
        parcelas = list((await s.execute(select(Parcela).order_by(Parcela.competencia))).scalars())
        assert [(p.valor, p.competencia) for p in parcelas] == [
            (Decimal("39.90"), date(2026, 4, 1)),
            (Decimal("39.90"), date(2026, 5, 1)),
        ]
        # A compra de março não pesa no orçamento de março, só no de abril.
        assert (await relatorios.resumo_mensal(s, Mes(2026, 3), HOJE)).orcamento.saldo == 0


async def test_inicio_padrao_pula_para_o_mes_seguinte_se_o_dia_ja_passou(
    fabrica: async_sessionmaker[AsyncSession],
) -> None:
    clock = f.relogio(HOJE)
    async with fabrica() as s, s.begin():
        ana = await f.membro(s)
        rec = await svc.criar(s, clock, ana, dados(dia=5))
        assert rec.inicio == date(2026, 4, 1)
        assert await _lancamentos(s) == []
        assert svc.proxima_data(rec) == date(2026, 4, 5)
        # Pedindo este mês de propósito, lança com a data que já passou.
        await svc.criar(s, clock, ana, dados(dia=5, descricao="Escola", inicio=Mes(2026, 3)))
        assert [x.data_operacao for x in await _lancamentos(s)] == [date(2026, 3, 5)]
        with pytest.raises(DataInvalida):
            await svc.criar(s, clock, ana, dados(inicio=Mes(2026, 2)))


async def test_gerar_e_idempotente_e_recupera_meses_parados(
    fabrica: async_sessionmaker[AsyncSession],
) -> None:
    clock = f.relogio(HOJE)
    async with fabrica() as s, s.begin():
        ana = await f.membro(s)
        await svc.criar(s, clock, ana, dados(dia=31))
        assert await svc.gerar(s, clock) == []
        # O sistema ficou parado em abril e maio; volta em junho.
        clock = f.relogio(date(2026, 6, 1))
        criados = await svc.gerar(s, clock)
        assert [x.data_operacao for x in criados] == [
            date(2026, 4, 30),
            date(2026, 5, 31),
            date(2026, 6, 30),
        ]
        assert await svc.gerar(s, clock) == []
        assert len(await _lancamentos(s)) == 4


async def test_banco_impede_dois_lancamentos_no_mesmo_mes(
    fabrica: async_sessionmaker[AsyncSession],
) -> None:
    clock = f.relogio(HOJE)
    async with fabrica() as s:
        async with s.begin():
            ana = await f.membro(s)
            rec = await svc.criar(s, clock, ana, dados())
        with pytest.raises(IntegrityError):
            async with s.begin():
                await s.execute(
                    text(
                        "INSERT INTO lancamentos (tipo, valor_total, descricao,"
                        " descricao_normalizada, membro_id, criado_por, data_operacao, status,"
                        " origem, recorrencia_id, recorrencia_mes) VALUES ('DEBITO', 1, 'x', 'x',"
                        " :m, :m, '2026-03-20', 'ATIVO', 'RECORRENCIA', :r, '2026-03-01')"
                    ),
                    {"m": ana.id, "r": rec.id},
                )


async def test_cancelar_o_mes_nao_gera_de_novo_e_editar_vale_para_os_proximos(
    fabrica: async_sessionmaker[AsyncSession],
) -> None:
    clock = f.relogio(HOJE)
    async with fabrica() as s, s.begin():
        ana = await f.membro(s)
        cat = await f.categoria(s, "Moradia")
        rec = await svc.criar(s, clock, ana, dados(categoria_id=cat.id))
        (marco,) = await _lancamentos(s)
        await svc_lanc.cancelar(s, clock, ana, marco.id, origem=Origem.WEB)
        assert await svc.gerar(s, clock) == []

        await svc.editar(s, clock, ana, rec.id, dados(valor="1600.00", dia=20, fim=Mes(2026, 4)))
        assert marco.valor_total == Decimal("1500.00")  # o já lançado não muda
        with pytest.raises(OperacaoInvalida):
            await svc.editar(s, clock, ana, rec.id, dados(TipoLancamento.RECEITA))
        cat.ativa = False  # categoria desativada depois: sai sem categoria
        (abril,) = await svc.gerar(s, f.relogio(date(2026, 4, 1)))
        assert (abril.valor_total, abril.data_operacao) == (Decimal("1600.00"), date(2026, 4, 20))
        assert abril.categoria_id is None
        # Terminou em abril.
        assert await svc.gerar(s, f.relogio(date(2026, 5, 2))) == []
        assert svc.proxima_data(rec) is None


async def test_encerrar_e_reativar_pula_os_meses_parados(
    fabrica: async_sessionmaker[AsyncSession],
) -> None:
    clock = f.relogio(HOJE)
    async with fabrica() as s, s.begin():
        ana = await f.membro(s)
        rec = await svc.criar(s, clock, ana, dados())
        await svc.definir_ativa(s, clock, ana, rec.id, False)
        assert await svc.gerar(s, f.relogio(date(2026, 5, 3))) == []
        assert len(await _lancamentos(s)) == 1  # o já lançado fica
        await svc.definir_ativa(s, f.relogio(date(2026, 6, 20)), ana, rec.id, True)
        datas = [x.data_operacao for x in await _lancamentos(s)]
        assert datas == [date(2026, 3, 15), date(2026, 6, 15)]


async def test_permissoes_e_tipo(fabrica: async_sessionmaker[AsyncSession]) -> None:
    clock = f.relogio(HOJE)
    async with fabrica() as s, s.begin():
        ana, bia, adm = (
            await f.membro(s),
            await f.membro(s, "Bia"),
            await f.membro(s, "Adm", admin=True),
        )
        with pytest.raises(SemPermissao):
            await svc.criar(s, clock, ana, dados(), titular_id=bia.id)
        rec = await svc.criar(s, clock, adm, dados(), titular_id=bia.id)
        (lanc,) = await _lancamentos(s)
        assert (lanc.membro_id, lanc.criado_por) == (bia.id, adm.id)
        with pytest.raises(SemPermissao):
            await svc.editar(s, clock, ana, rec.id, dados())
        with pytest.raises(SemPermissao):
            await svc.definir_ativa(s, clock, ana, rec.id, False)
        with pytest.raises(OperacaoInvalida):  # o tipo não muda na edição
            await svc.editar(s, clock, bia, rec.id, dados(TipoLancamento.CREDITO))
        await svc.editar(s, clock, bia, rec.id, dados(valor="10.00"))


async def test_titular_inativo_nao_recebe_e_criador_rebaixado_assina_o_titular(
    fabrica: async_sessionmaker[AsyncSession],
) -> None:
    clock = f.relogio(HOJE)
    async with fabrica() as s, s.begin():
        bia, adm = await f.membro(s, "Bia"), await f.membro(s, "Adm", admin=True)
        await svc.criar(s, clock, adm, dados(), titular_id=bia.id)
        adm.papel = "membro"
        (abril,) = await svc.gerar(s, f.relogio(date(2026, 4, 1)))
        assert abril.criado_por == bia.id
        bia.ativo = False
        assert await svc.gerar(s, f.relogio(date(2026, 5, 1))) == []
        bia.ativo = True
        (junho,) = await svc.gerar(s, f.relogio(date(2026, 6, 1)))
        assert junho.data_operacao == date(2026, 6, 15)  # maio não volta


async def test_desfazer_do_bot_ignora_lancamento_de_recorrencia(
    fabrica: async_sessionmaker[AsyncSession],
) -> None:
    clock = f.relogio(HOJE)
    async with fabrica() as s, s.begin():
        ana = await f.membro(s)
        await svc.criar(s, clock, ana, dados())
        assert await svc_lanc.ultimo_desfazivel(s, clock, await s.get_one(Membro, ana.id)) is None
