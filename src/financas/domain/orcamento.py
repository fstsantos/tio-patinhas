"""Saldo mensal e orçamento diário (spec 5.5).

    Saldo = R − D − P
    Orçamento diário = Saldo ÷ dias  (se Saldo > 0 e mês não encerrado), senão 0

R = receitas do mês, D = débitos do mês, P = parcelas com mês de referência no mês.
Compras no crédito feitas no mês não entram (a primeira parcela é de M + 1).
Esta é a única função de cálculo usada pelo bot, pela web e pelo resumo anual.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from financas.domain.calendario import Mes, SituacaoMes
from financas.domain.dinheiro import arredondar

ZERO = Decimal("0.00")


@dataclass(frozen=True, slots=True)
class Orcamento:
    mes: Mes
    situacao: SituacaoMes
    receitas: Decimal
    debitos: Decimal
    parcelas: Decimal
    saldo: Decimal
    dias_restantes: int
    # None para mês encerrado (não se exibe orçamento diário).
    orcamento_diario: Decimal | None

    @property
    def despesas_consideradas(self) -> Decimal:
        return self.debitos + self.parcelas

    @property
    def deficit(self) -> bool:
        return self.saldo < 0


def calcular_orcamento(
    mes: Mes, hoje: date, *, receitas: Decimal, debitos: Decimal, parcelas: Decimal
) -> Orcamento:
    situacao = mes.situacao(hoje)
    saldo = receitas - debitos - parcelas
    dias = mes.dias_restantes(hoje)
    diario: Decimal | None
    if situacao is SituacaoMes.ENCERRADO:
        diario = None
    elif saldo > 0:
        diario = arredondar(saldo / dias)
    else:
        diario = ZERO
    return Orcamento(
        mes=mes,
        situacao=situacao,
        receitas=receitas,
        debitos=debitos,
        parcelas=parcelas,
        saldo=saldo,
        dias_restantes=dias,
        orcamento_diario=diario,
    )
