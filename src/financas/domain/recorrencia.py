"""Recorrências mensais: em que dia caem e quais meses falta gerar.

Uma recorrência cai num dia fixo do mês. Em mês mais curto que esse dia (31 em abril,
29 a 31 em fevereiro), cai no último dia do mês.
"""

from __future__ import annotations

from datetime import date

from financas.domain.calendario import Mes
from financas.domain.erros import DataInvalida

DIA_MIN, DIA_MAX = 1, 31


def validar_dia(dia: int) -> int:
    if not DIA_MIN <= dia <= DIA_MAX:
        raise DataInvalida(f"Dia do mês inválido: {dia}.", sugestao="Use um dia de 1 a 31.")
    return dia


def validar_periodo(inicio: Mes, fim: Mes | None) -> None:
    if fim is not None and fim < inicio:
        raise DataInvalida("O último mês da recorrência não pode ser antes do primeiro.")


def data_no_mes(mes: Mes, dia: int) -> date:
    """Data em que a recorrência cai em ``mes`` (último dia se o mês for mais curto)."""
    return date(mes.ano, mes.mes, min(validar_dia(dia), mes.total_dias))


def meses_a_gerar(inicio: Mes, fim: Mes | None, gerada_ate: Mes | None, atual: Mes) -> list[Mes]:
    """Meses ainda não tratados, do início (ou do seguinte ao último gerado) até o atual."""
    primeiro = inicio if gerada_ate is None else max(inicio, gerada_ate.somar(1))
    ultimo = atual if fim is None else min(atual, fim)
    meses: list[Mes] = []
    while primeiro <= ultimo:
        meses.append(primeiro)
        primeiro = primeiro.somar(1)
    return meses
