from datetime import date

import pytest

from financas.domain.calendario import Mes, SituacaoMes, competencia_parcela
from financas.domain.erros import DataInvalida


def test_somar_virada_de_ano() -> None:
    assert Mes(2026, 12).somar(1) == Mes(2027, 1)
    assert Mes(2026, 1).somar(-1) == Mes(2025, 12)
    assert Mes(2026, 3).somar(48) == Mes(2030, 3)


@pytest.mark.parametrize(
    ("mes", "dias"),
    [
        (Mes(2026, 2), 28),
        (Mes(2028, 2), 29),
        (Mes(2100, 2), 28),
        (Mes(2000, 2), 29),
        (Mes(2026, 4), 30),
        (Mes(2026, 1), 31),
    ],
)
def test_total_dias(mes: Mes, dias: int) -> None:
    assert mes.total_dias == dias


def test_intervalo_semiaberto() -> None:
    inicio, fim = Mes(2026, 12).intervalo()
    assert (inicio, fim) == (date(2026, 12, 1), date(2027, 1, 1))
    assert Mes(2026, 12).contem(date(2026, 12, 31))
    assert not Mes(2026, 12).contem(date(2027, 1, 1))


def test_dias_restantes_incluindo_hoje() -> None:
    m = Mes(2026, 3)
    assert m.dias_restantes(date(2026, 3, 1)) == 31
    assert m.dias_restantes(date(2026, 3, 20)) == 12
    assert m.dias_restantes(date(2026, 3, 31)) == 1
    assert m.dias_restantes(date(2026, 4, 1)) == 0  # encerrado
    assert m.dias_restantes(date(2026, 2, 10)) == 31  # futuro


def test_situacao() -> None:
    hoje = date(2026, 3, 15)
    assert Mes(2026, 2).situacao(hoje) is SituacaoMes.ENCERRADO
    assert Mes(2026, 3).situacao(hoje) is SituacaoMes.CORRENTE
    assert Mes(2026, 4).situacao(hoje) is SituacaoMes.FUTURO


def test_parse_e_formatos() -> None:
    m = Mes.parse("04/2026")
    assert m == Mes(2026, 4)
    assert m.abreviado == "abr/2026"
    assert m.por_extenso == "abril de 2026"
    assert str(Mes.parse("4/2026")) == "04/2026"
    for invalido in ("13/2026", "2026-04", "abr/2026", "00/2026"):
        with pytest.raises(DataInvalida):
            Mes.parse(invalido)


@pytest.mark.parametrize(
    ("compra", "numero", "competencia"),
    [
        (date(2026, 3, 20), 1, date(2026, 4, 1)),
        (date(2026, 3, 1), 1, date(2026, 4, 1)),  # primeiro dia do mês
        (date(2026, 3, 31), 1, date(2026, 4, 1)),  # último dia do mês
        (date(2026, 1, 31), 1, date(2026, 2, 1)),  # 31/jan → fevereiro (sem estouro de dia)
        (date(2026, 12, 15), 1, date(2027, 1, 1)),  # virada de ano
        (date(2026, 12, 15), 12, date(2027, 12, 1)),
        (date(2026, 3, 20), 48, date(2030, 3, 1)),
    ],
)
def test_competencia_parcela(compra: date, numero: int, competencia: date) -> None:
    assert competencia_parcela(compra, numero) == competencia


@pytest.mark.parametrize(
    ("compra", "primeira", "numero", "competencia"),
    [
        (date(2026, 11, 5), date(2026, 11, 1), 1, date(2026, 11, 1)),  # mesmo mês (M + 0)
        (date(2026, 11, 5), date(2026, 11, 1), 3, date(2027, 1, 1)),  # virada de ano
        (date(2026, 3, 30), date(2026, 5, 1), 1, date(2026, 5, 1)),  # M + 2
        (date(2026, 3, 30), date(2026, 5, 1), 2, date(2026, 6, 1)),
    ],
)
def test_competencia_parcela_com_primeira_informada(
    compra: date, primeira: date, numero: int, competencia: date
) -> None:
    assert competencia_parcela(compra, numero, primeira) == competencia
