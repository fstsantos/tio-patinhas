import pytest

from financas.domain.erros import DescricaoInvalida
from financas.domain.texto import normalizar, validar_descricao


def test_normalizar() -> None:
    assert normalizar("Débito") == normalizar("DEBITO") == normalizar("debito") == "debito"
    assert normalizar("  Crédito   Já  ") == "credito ja"
    assert normalizar("Últimos") == "ultimos"


def test_validar_descricao() -> None:
    assert validar_descricao("  compras   de mercado ") == "compras de mercado"
    assert validar_descricao("x" * 120) == "x" * 120
    for invalida in ("", "   ", "x" * 121):
        with pytest.raises(DescricaoInvalida):
            validar_descricao(invalida)
