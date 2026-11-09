from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from financas.domain.calendario import Mes
from financas.domain.erros import ParcelasInvalidas, ValorInvalido
from financas.domain.parcelamento import distribuir, gerar_parcelas

centavos = st.integers(min_value=1, max_value=100_000_000)
qtds = st.integers(min_value=1, max_value=48)


def test_exemplo_da_especificacao() -> None:
    assert distribuir(Decimal("100.00"), 3) == [
        Decimal("33.34"),
        Decimal("33.33"),
        Decimal("33.33"),
    ]


def test_compra_mercado_livre() -> None:
    parcelas = gerar_parcelas(Decimal("1000.00"), 10, date(2026, 3, 20))
    assert [p.valor for p in parcelas] == [Decimal("100.00")] * 10
    assert [Mes.de_data(p.competencia).abreviado for p in parcelas] == [
        "abr/2026", "mai/2026", "jun/2026", "jul/2026", "ago/2026",
        "set/2026", "out/2026", "nov/2026", "dez/2026", "jan/2027",
    ]  # fmt: skip
    assert [p.numero for p in parcelas] == list(range(1, 11))


@given(centavos, qtds)
def test_soma_igual_total_e_diferenca_maxima_um_centavo(total_c: int, n: int) -> None:
    total = Decimal(total_c) / 100
    if total_c < n:
        with pytest.raises(ParcelasInvalidas):
            distribuir(total, n)
        return
    valores = distribuir(total, n)
    assert len(valores) == n
    assert sum(valores) == total
    assert max(valores) - min(valores) <= Decimal("0.01")
    assert all(v == v.quantize(Decimal("0.01")) and v > 0 for v in valores)
    # centavos extras ficam nas primeiras parcelas (ordem não crescente)
    assert valores == sorted(valores, reverse=True)


@given(
    centavos.filter(lambda c: c >= 48),
    qtds,
    st.dates(min_value=date(2000, 1, 1), max_value=date(2099, 12, 31)),
)
def test_meses_consecutivos_a_partir_do_seguinte(total_c: int, n: int, compra: date) -> None:
    parcelas = gerar_parcelas(Decimal(total_c) / 100, n, compra)
    mes_compra = Mes.de_data(compra)
    for k, p in enumerate(parcelas, start=1):
        assert p.competencia == mes_compra.somar(k).primeiro_dia
        assert p.competencia.day == 1


@pytest.mark.parametrize("n", [0, 49, -1])
def test_quantidade_fora_do_intervalo(n: int) -> None:
    with pytest.raises(ParcelasInvalidas):
        distribuir(Decimal("100.00"), n)


def test_uma_e_quarenta_e_oito_parcelas() -> None:
    assert distribuir(Decimal("50.00"), 1) == [Decimal("50.00")]
    v = distribuir(Decimal("1000.00"), 48)
    assert v[:16] == [Decimal("20.84")] * 16 and v[16:] == [Decimal("20.83")] * 32


@pytest.mark.parametrize("total", ["0", "-10", "10.001", "1000000.01"])
def test_total_invalido(total: str) -> None:
    with pytest.raises(ValorInvalido):
        distribuir(Decimal(total), 2)


def test_compra_retroativa() -> None:
    parcelas = gerar_parcelas(Decimal("300.00"), 3, date(2026, 2, 10))
    assert [p.competencia for p in parcelas] == [
        date(2026, 3, 1),
        date(2026, 4, 1),
        date(2026, 5, 1),
    ]
