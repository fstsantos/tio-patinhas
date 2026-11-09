"""Relatórios recalculados do estado atual (spec 3.4–3.6, 5.5).

Bot, dashboard web e resumo anual usam estas funções, e todas passam por
``domain.orcamento.calcular_orcamento``. Somente lançamentos ativos e parcelas
pendentes são considerados; o valor total de compras no crédito nunca entra no saldo.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, extract, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from financas.db.models import (
    Categoria,
    Lancamento,
    Membro,
    Parcela,
    StatusLancamento,
    StatusParcela,
    TipoLancamento,
)
from financas.domain.calendario import Mes
from financas.domain.orcamento import ZERO, Orcamento, calcular_orcamento

SEM_CATEGORIA = "Sem categoria"


@dataclass(frozen=True, slots=True)
class ItemLancamento:
    id: int
    tipo: str
    descricao: str
    descricao_normalizada: str
    valor: Decimal
    data: date
    membro: str
    categoria: str | None


@dataclass(frozen=True, slots=True)
class ItemParcela:
    lancamento_id: int
    descricao: str
    descricao_normalizada: str
    valor: Decimal
    numero: int
    total_parcelas: int
    competencia: Mes
    data_compra: date
    membro: str
    categoria: str | None


@dataclass(frozen=True, slots=True)
class Grupo:
    nome: str
    total: Decimal
    quantidade: int


@dataclass(frozen=True, slots=True)
class ResumoMensal:
    orcamento: Orcamento
    receitas: list[ItemLancamento]
    debitos: list[ItemLancamento]
    parcelas: list[ItemParcela]
    # Informativo: compras no crédito feitas no mês e seu impacto nos meses seguintes.
    compras_credito: list[ItemLancamento]
    impacto_futuro: list[tuple[Mes, Decimal]]
    por_categoria: list[Grupo]
    por_descricao: list[Grupo]

    @property
    def total_credito_contratado(self) -> Decimal:
        return sum((c.valor for c in self.compras_credito), ZERO)


@dataclass(frozen=True, slots=True)
class MesAnual:
    orcamento: Orcamento
    saldo_acumulado: Decimal


@dataclass(frozen=True, slots=True)
class ResumoAnual:
    ano: int
    meses: list[MesAnual]

    @property
    def receitas(self) -> Decimal:
        return sum((m.orcamento.receitas for m in self.meses), ZERO)

    @property
    def debitos(self) -> Decimal:
        return sum((m.orcamento.debitos for m in self.meses), ZERO)

    @property
    def parcelas(self) -> Decimal:
        return sum((m.orcamento.parcelas for m in self.meses), ZERO)

    @property
    def despesas(self) -> Decimal:
        return self.debitos + self.parcelas

    @property
    def saldo(self) -> Decimal:
        return self.receitas - self.despesas


@dataclass(frozen=True, slots=True)
class BlocoCredito:
    mes: Mes
    parcelas: list[ItemParcela]

    @property
    def subtotal(self) -> Decimal:
        return sum((p.valor for p in self.parcelas), ZERO)


@dataclass(frozen=True, slots=True)
class ResumoCredito:
    blocos: list[BlocoCredito] = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return sum((b.subtotal for b in self.blocos), ZERO)


# ------------------------------------------------------------------ consultas

_Titular = aliased(Membro)


def _consulta_lancamentos() -> Select[Any]:
    return (
        select(Lancamento, _Titular.nome, Categoria.nome)
        .join(_Titular, _Titular.id == Lancamento.membro_id)
        .outerjoin(Categoria, Categoria.id == Lancamento.categoria_id)
        .where(Lancamento.status == StatusLancamento.ATIVO.value)
    )


def _consulta_parcelas() -> Select[Any]:
    return (
        select(Parcela, Lancamento, _Titular.nome, Categoria.nome)
        .join(Lancamento, Lancamento.id == Parcela.lancamento_id)
        .join(_Titular, _Titular.id == Lancamento.membro_id)
        .outerjoin(Categoria, Categoria.id == Lancamento.categoria_id)
        .where(
            Parcela.status == StatusParcela.PENDENTE.value,
            Lancamento.status == StatusLancamento.ATIVO.value,
        )
    )


def _item(lanc: Lancamento, membro: str, categoria: str | None) -> ItemLancamento:
    return ItemLancamento(
        lanc.id,
        lanc.tipo,
        lanc.descricao,
        lanc.descricao_normalizada,
        lanc.valor_total,
        lanc.data_operacao,
        membro,
        categoria,
    )


def _item_parcela(p: Parcela, lanc: Lancamento, membro: str, categoria: str | None) -> ItemParcela:
    assert lanc.qtd_parcelas is not None
    return ItemParcela(
        lanc.id,
        lanc.descricao,
        lanc.descricao_normalizada,
        p.valor,
        p.numero,
        lanc.qtd_parcelas,
        Mes.de_data(p.competencia),
        lanc.data_operacao,
        membro,
        categoria,
    )


def _agrupar(chaves_valores: list[tuple[str, Decimal]]) -> list[Grupo]:
    totais: dict[str, Decimal] = defaultdict(lambda: ZERO)
    qtd: dict[str, int] = defaultdict(int)
    for chave, valor in chaves_valores:
        totais[chave] += valor
        qtd[chave] += 1
    return sorted(
        (Grupo(k, v, qtd[k]) for k, v in totais.items()), key=lambda g: (-g.total, g.nome)
    )


async def resumo_mensal(
    s: AsyncSession, mes: Mes, hoje: date, *, membro_id: int | None = None
) -> ResumoMensal:
    inicio, fim = mes.intervalo()
    q = _consulta_lancamentos().where(
        Lancamento.data_operacao >= inicio, Lancamento.data_operacao < fim
    )
    if membro_id is not None:
        q = q.where(Lancamento.membro_id == membro_id)
    q = q.order_by(Lancamento.data_operacao, Lancamento.id)
    receitas: list[ItemLancamento] = []
    debitos: list[ItemLancamento] = []
    compras: list[ItemLancamento] = []
    for lanc, membro, categoria in (await s.execute(q)).tuples():
        item = _item(lanc, membro, categoria)
        {
            TipoLancamento.RECEITA.value: receitas,
            TipoLancamento.DEBITO.value: debitos,
            TipoLancamento.CREDITO.value: compras,
        }[lanc.tipo].append(item)

    qp = _consulta_parcelas().where(Parcela.competencia == mes.primeiro_dia)
    if membro_id is not None:
        qp = qp.where(Lancamento.membro_id == membro_id)
    qp = qp.order_by(Lancamento.data_operacao, Lancamento.id)
    parcelas = [_item_parcela(*row) for row in (await s.execute(qp)).tuples()]

    impacto: list[tuple[Mes, Decimal]] = []
    if compras:
        qi = (
            select(Parcela.competencia, func.sum(Parcela.valor))
            .where(
                Parcela.lancamento_id.in_([c.id for c in compras]),
                Parcela.status == StatusParcela.PENDENTE.value,
            )
            .group_by(Parcela.competencia)
            .order_by(Parcela.competencia)
        )
        impacto = [(Mes.de_data(c), Decimal(v)) for c, v in (await s.execute(qi)).tuples()]

    orcamento = calcular_orcamento(
        mes,
        hoje,
        receitas=sum((r.valor for r in receitas), ZERO),
        debitos=sum((d.valor for d in debitos), ZERO),
        parcelas=sum((p.valor for p in parcelas), ZERO),
    )
    despesas = [(d.categoria, d.descricao_normalizada, d.valor) for d in debitos] + [
        (p.categoria, p.descricao_normalizada, p.valor) for p in parcelas
    ]
    tem_categoria = any(c is not None for c, _, _ in despesas)
    return ResumoMensal(
        orcamento=orcamento,
        receitas=receitas,
        debitos=debitos,
        parcelas=parcelas,
        compras_credito=compras,
        impacto_futuro=impacto,
        por_categoria=(
            _agrupar([(c or SEM_CATEGORIA, v) for c, _, v in despesas]) if tem_categoria else []
        ),
        por_descricao=_agrupar([(d, v) for _, d, v in despesas]),
    )


async def resumo_anual(
    s: AsyncSession, ano: int, hoje: date, *, membro_id: int | None = None
) -> ResumoAnual:
    inicio, fim = date(ano, 1, 1), date(ano + 1, 1, 1)
    mes_col = extract("month", Lancamento.data_operacao)
    ql = (
        select(mes_col, Lancamento.tipo, func.sum(Lancamento.valor_total))
        .where(
            Lancamento.status == StatusLancamento.ATIVO.value,
            Lancamento.tipo.in_([TipoLancamento.RECEITA.value, TipoLancamento.DEBITO.value]),
            Lancamento.data_operacao >= inicio,
            Lancamento.data_operacao < fim,
        )
        .group_by(mes_col, Lancamento.tipo)
    )
    qp = (
        select(extract("month", Parcela.competencia), func.sum(Parcela.valor))
        .join(Lancamento, Lancamento.id == Parcela.lancamento_id)
        .where(
            Parcela.status == StatusParcela.PENDENTE.value,
            Lancamento.status == StatusLancamento.ATIVO.value,
            Parcela.competencia >= inicio,
            Parcela.competencia < fim,
        )
        .group_by(extract("month", Parcela.competencia))
    )
    if membro_id is not None:
        ql = ql.where(Lancamento.membro_id == membro_id)
        qp = qp.where(Lancamento.membro_id == membro_id)

    receitas: dict[int, Decimal] = defaultdict(lambda: ZERO)
    debitos: dict[int, Decimal] = defaultdict(lambda: ZERO)
    parcelas: dict[int, Decimal] = defaultdict(lambda: ZERO)
    for m, tipo, total in (await s.execute(ql)).tuples():
        (receitas if tipo == TipoLancamento.RECEITA.value else debitos)[int(m)] = Decimal(total)
    for m, total in (await s.execute(qp)).tuples():
        parcelas[int(m)] = Decimal(total)

    meses: list[MesAnual] = []
    acumulado = ZERO
    for m in range(1, 13):
        o = calcular_orcamento(
            Mes(ano, m), hoje, receitas=receitas[m], debitos=debitos[m], parcelas=parcelas[m]
        )
        acumulado += o.saldo
        meses.append(MesAnual(o, acumulado))
    return ResumoAnual(ano, meses)


async def resumo_credito(
    s: AsyncSession, hoje: date, *, membro_id: int | None = None
) -> ResumoCredito:
    """Parcelas pendentes do mês corrente em diante, agrupadas por mês de referência."""
    q = _consulta_parcelas().where(Parcela.competencia >= Mes.de_data(hoje).primeiro_dia)
    if membro_id is not None:
        q = q.where(Lancamento.membro_id == membro_id)
    q = q.order_by(Parcela.competencia, Lancamento.data_operacao, Lancamento.id)
    blocos: dict[Mes, list[ItemParcela]] = {}
    for row in (await s.execute(q)).tuples():
        item = _item_parcela(*row)
        blocos.setdefault(item.competencia, []).append(item)
    return ResumoCredito([BlocoCredito(m, ps) for m, ps in blocos.items()])
