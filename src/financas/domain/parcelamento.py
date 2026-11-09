"""Parcelamento de compras no crédito (spec 5.6).

- A parcela k (1..n) pertence ao mês da compra + k.
- Distribuição de centavos: base = total ÷ n truncada; o resto (em centavos)
  vai para as primeiras parcelas, R$ 0,01 cada. A soma é sempre igual ao total.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from financas.domain.calendario import competencia_parcela
from financas.domain.dinheiro import validar_limites
from financas.domain.erros import ParcelasInvalidas

PARCELAS_MIN = 1
PARCELAS_MAX = 48


@dataclass(frozen=True, slots=True)
class ParcelaPlanejada:
    numero: int
    valor: Decimal
    competencia: date  # primeiro dia do mês de referência


def validar_qtd_parcelas(n: int) -> int:
    if not PARCELAS_MIN <= n <= PARCELAS_MAX:
        raise ParcelasInvalidas(
            f"Quantidade de parcelas inválida: {n}.",
            sugestao=f"Use de {PARCELAS_MIN} a {PARCELAS_MAX} parcelas.",
        )
    return n


def distribuir(total: Decimal, n: int) -> list[Decimal]:
    """Divide ``total`` em ``n`` valores cuja soma é exatamente ``total``."""
    total = validar_limites(total)
    validar_qtd_parcelas(n)
    total_centavos = int(total * 100)
    base, resto = divmod(total_centavos, n)
    if base == 0:
        raise ParcelasInvalidas(
            "Cada parcela precisa ter pelo menos R$ 0,01.",
            sugestao="Reduza a quantidade de parcelas.",
        )
    return [Decimal(base + (1 if i < resto else 0)) / 100 for i in range(n)]


def gerar_parcelas(total: Decimal, n: int, data_compra: date) -> list[ParcelaPlanejada]:
    valores = distribuir(total, n)
    return [
        ParcelaPlanejada(
            numero=k,
            valor=v.quantize(Decimal("0.01")),
            competencia=competencia_parcela(data_compra, k),
        )
        for k, v in enumerate(valores, start=1)
    ]
