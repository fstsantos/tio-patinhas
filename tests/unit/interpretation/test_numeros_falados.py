import pytest

from financas.interpretation.numeros_falados_ptbr import normalizar_numeros


@pytest.mark.parametrize(
    ("falado", "esperado"),
    [
        ("dez reais e cinquenta", "10,50"),
        ("dez reais e cinquenta centavos", "10,50"),
        ("dez e cinquenta", "10,50"),
        ("mil e duzentos", "1200"),
        ("mil e duzentos reais", "1200"),
        ("mil duzentos e cinquenta reais", "1250"),
        ("dois mil e quinhentos", "2500"),
        ("cento e vinte e cinco reais e noventa e nove centavos", "125,99"),
        ("vinte e cinco reais", "25"),
        ("cem reais", "100"),
        ("cinquenta centavos", "0,50"),
        ("um real e dez centavos", "1,10"),
        ("um milhão", "1000000"),
        ("milhão", "milhão"),
        ("em dez vezes", "em 10x"),
        ("em 10 vezes", "em 10x"),
        ("dez parcelas de cem", "10x de 100"),
        ("uma vez", "1x"),
        ("10 reais e 50 centavos", "10,50"),
        ("R$ 10,50", "R$ 10,50"),
        ("dezessete", "17"),
        ("trinta e um", "31"),
    ],
)
def test_normalizar(falado: str, esperado: str) -> None:
    assert normalizar_numeros(falado) == esperado


def test_frase_completa_preserva_palavras() -> None:
    assert (
        normalizar_numeros("Registra um débito de dez reais e cinquenta no mercado.")
        == "Registra um débito de 10,50 no mercado"
    )


def test_artigo_um_preservado() -> None:
    assert normalizar_numeros("comprei uma televisão") == "comprei uma televisão"
    assert normalizar_numeros("um ganho de três mil") == "um ganho de 3000"
