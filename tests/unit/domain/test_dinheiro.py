from decimal import Decimal

import pytest

from financas.domain.dinheiro import arredondar, formatar_brl, parece_valor, parse_valor
from financas.domain.erros import ValorInvalido


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("10,50", "10.50"),
        ("10,5", "10.50"),
        ("10", "10.00"),
        ("0,01", "0.01"),
        ("1.000", "1000.00"),
        ("1.000,00", "1000.00"),
        ("12.345,67", "12345.67"),
        ("1.234.567", None),  # acima do limite
        ("R$ 10,50", "10.50"),
        ("r$10,50", "10.50"),
        ("R$1.000.000,00", "1000000.00"),
        ("1000000", "1000000.00"),
    ],
)
def test_parse_valor(entrada: str, esperado: str | None) -> None:
    if esperado is None:
        with pytest.raises(ValorInvalido):
            parse_valor(entrada)
    else:
        assert parse_valor(entrada) == Decimal(esperado)


@pytest.mark.parametrize(
    "entrada",
    [
        "10.50",
        "1.5",
        "10,505",
        "0",
        "0,00",
        "-10",
        "-1,00",
        "1.00.000",
        "10.5000",
        "abc",
        "",
        "R$",
        "10,",
        ",50",
        "1,000,00",
        "1000000,01",
        "1.0000",
    ],
)
def test_parse_valor_rejeita(entrada: str) -> None:
    with pytest.raises(ValorInvalido):
        parse_valor(entrada)


@pytest.mark.parametrize(("entrada", "sugestao"), [("10.50", "10,50"), ("1.5", "1,50")])
def test_ponto_decimal_sugere_virgula(entrada: str, sugestao: str) -> None:
    with pytest.raises(ValorInvalido) as e:
        parse_valor(entrada)
    assert e.value.sugestao == f"Você quis dizer {sugestao}?"


def test_mais_de_duas_casas_rejeitado_nao_arredondado() -> None:
    with pytest.raises(ValorInvalido, match="mais de duas casas"):
        parse_valor("10,505")


@pytest.mark.parametrize(
    ("valor", "texto"),
    [
        ("0", "R$ 0,00"),
        ("0.5", "R$ 0,50"),
        ("1234.56", "R$ 1.234,56"),
        ("1000000", "R$ 1.000.000,00"),
        ("-45.1", "-R$ 45,10"),
        ("999.999", "R$ 1.000,00"),
    ],
)
def test_formatar_brl(valor: str, texto: str) -> None:
    assert formatar_brl(Decimal(valor)) == texto


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [("0.005", "0.01"), ("0.004", "0.00"), ("33.335", "33.34"), ("-0.005", "-0.01")],
)
def test_arredondar_half_up(valor: str, esperado: str) -> None:
    assert arredondar(Decimal(valor)) == Decimal(esperado)


@pytest.mark.parametrize(
    ("token", "esperado"),
    [
        ("10,50", True),
        ("R$10", True),
        ("10.50", True),
        ("mercado", False),
        ("10x", False),
        ("", False),
        ("R$", False),
    ],
)
def test_parece_valor(token: str, esperado: bool) -> None:
    assert parece_valor(token) is esperado
