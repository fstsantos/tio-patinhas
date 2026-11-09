"""Valores monetários em BRL.

Regras (spec 5.1):
- vírgula é o separador decimal; ponto só é aceito como separador de milhar
  em grupos exatos de três dígitos (``1.000``, ``12.345,67``);
- mais de duas casas decimais são rejeitadas, nunca arredondadas;
- limites de R$ 0,01 a R$ 1.000.000,00;
- ``float`` é proibido: tudo é ``Decimal``.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

from financas.domain.erros import ValorInvalido

CENTAVO = Decimal("0.01")
VALOR_MINIMO = Decimal("0.01")
VALOR_MAXIMO = Decimal("1000000.00")

_PREFIXO_RS = re.compile(r"^r\$\s*", re.IGNORECASE)
_SEM_MILHAR = re.compile(r"^(?P<inteiro>\d+)(?:,(?P<dec>\d+))?$")
_COM_MILHAR = re.compile(r"^(?P<inteiro>\d{1,3}(?:\.\d{3})+)(?:,(?P<dec>\d+))?$")
# Ponto usado como decimal (ex.: "10.50", "1.5") — rejeitado com sugestão.
_PONTO_DECIMAL = re.compile(r"^(?P<inteiro>\d+)\.(?P<dec>\d{1,2})$")


def parece_valor(texto: str) -> bool:
    """Indica se o token tem forma de valor monetário (válido ou não)."""
    t = _PREFIXO_RS.sub("", texto.strip())
    return bool(t) and bool(re.fullmatch(r"-?[\d.,]+", t)) and any(c.isdigit() for c in t)


def parse_valor(texto: str) -> Decimal:
    """Converte texto em pt-BR para ``Decimal`` com duas casas, validando limites."""
    bruto = texto.strip()
    t = _PREFIXO_RS.sub("", bruto).strip()
    if not t:
        raise ValorInvalido("Informe um valor.")
    if t.startswith("-"):
        raise ValorInvalido("O valor deve ser positivo.")

    m = _SEM_MILHAR.fullmatch(t) or _COM_MILHAR.fullmatch(t)
    if m is None:
        pd = _PONTO_DECIMAL.fullmatch(t)
        if pd is not None:
            sugestao = f"{pd['inteiro']},{pd['dec'].ljust(2, '0')}"
            raise ValorInvalido(f"Valor inválido: '{t}'.", sugestao=f"Você quis dizer {sugestao}?")
        raise ValorInvalido(
            f"Valor inválido: '{t}'.",
            sugestao="Use vírgula para os centavos, por exemplo 10,50 ou 1.234,56.",
        )

    decimais = m["dec"] or ""
    if len(decimais) > 2:
        raise ValorInvalido(
            f"Valor inválido: '{t}' tem mais de duas casas decimais.",
            sugestao="Informe no máximo dois dígitos de centavos.",
        )
    inteiro = m["inteiro"].replace(".", "")
    valor = Decimal(f"{inteiro}.{decimais.ljust(2, '0')}")
    validar_limites(valor)
    return valor


def validar_limites(valor: Decimal) -> Decimal:
    """Garante duas casas e o intervalo [0,01; 1.000.000,00]."""
    if valor != valor.quantize(CENTAVO):
        raise ValorInvalido("O valor deve ter no máximo duas casas decimais.")
    if valor < VALOR_MINIMO:
        raise ValorInvalido("O valor deve ser de pelo menos R$ 0,01.")
    if valor > VALOR_MAXIMO:
        raise ValorInvalido("O valor máximo permitido é R$ 1.000.000,00.")
    return valor.quantize(CENTAVO)


def arredondar(valor: Decimal) -> Decimal:
    """Arredonda para centavos, empate para cima (ROUND_HALF_UP)."""
    return valor.quantize(CENTAVO, rounding=ROUND_HALF_UP)


def formatar_brl(valor: Decimal) -> str:
    """Formata como ``R$ 1.234,56`` (negativos como ``-R$ 1.234,56``)."""
    q = arredondar(valor)
    sinal = "-" if q < 0 else ""
    inteiro, _, dec = f"{abs(q):.2f}".partition(".")
    grupos: list[str] = []
    while len(inteiro) > 3:
        grupos.insert(0, inteiro[-3:])
        inteiro = inteiro[:-3]
    grupos.insert(0, inteiro)
    return f"{sinal}R$ {'.'.join(grupos)},{dec}"
