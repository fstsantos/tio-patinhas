from __future__ import annotations

from datetime import date

import pytest

from financas.domain.calendario import Mes
from financas.domain.erros import DataInvalida
from financas.domain.recorrencia import data_no_mes, meses_a_gerar, validar_dia, validar_periodo


@pytest.mark.parametrize(
    ("mes", "dia", "esperado"),
    [
        (Mes(2026, 3), 5, date(2026, 3, 5)),
        (Mes(2026, 4), 31, date(2026, 4, 30)),
        (Mes(2026, 2), 30, date(2026, 2, 28)),
        (Mes(2028, 2), 31, date(2028, 2, 29)),
    ],
)
def test_data_no_mes_usa_ultimo_dia_em_mes_curto(mes: Mes, dia: int, esperado: date) -> None:
    assert data_no_mes(mes, dia) == esperado


@pytest.mark.parametrize("dia", [0, 32, -1])
def test_dia_invalido(dia: int) -> None:
    with pytest.raises(DataInvalida):
        validar_dia(dia)


def test_periodo_invalido() -> None:
    validar_periodo(Mes(2026, 3), None)
    validar_periodo(Mes(2026, 3), Mes(2026, 3))
    with pytest.raises(DataInvalida):
        validar_periodo(Mes(2026, 3), Mes(2026, 2))


def test_meses_a_gerar() -> None:
    marco, maio = Mes(2026, 3), Mes(2026, 5)
    # Nada gerado ainda: do início até o mês atual.
    assert meses_a_gerar(marco, None, None, maio) == [marco, Mes(2026, 4), maio]
    # Já gerada até abril: só maio.
    assert meses_a_gerar(marco, None, Mes(2026, 4), maio) == [maio]
    # Em dia.
    assert meses_a_gerar(marco, None, maio, maio) == []
    # Começa no futuro.
    assert meses_a_gerar(Mes(2026, 6), None, None, maio) == []
    # Termina antes do mês atual.
    assert meses_a_gerar(marco, Mes(2026, 4), None, maio) == [marco, Mes(2026, 4)]
    # Virada de ano.
    assert meses_a_gerar(Mes(2026, 12), None, Mes(2026, 12), Mes(2027, 1)) == [Mes(2027, 1)]
