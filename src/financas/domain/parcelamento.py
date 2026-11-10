"""Parcelamento de compras no crédito (spec 5.6).

- A parcela k (1..n) pertence ao mês da compra + k (regra padrão).
- Opcionalmente, a compra informa o mês da 1ª parcela (ex.: importação de fatura,
  em que o fechamento do cartão define o mês). Ele pode ser o próprio mês da compra
  ou até dois meses depois; a parcela k pertence a esse mês + (k − 1).
- Distribuição de centavos: base = total ÷ n truncada; o resto (em centavos)
  vai para as primeiras parcelas, R$ 0,01 cada. A soma é sempre igual ao total.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from financas.domain.calendario import Mes, competencia_parcela
from financas.domain.dinheiro import validar_limites
from financas.domain.erros import DataInvalida, ParcelasInvalidas

PARCELAS_MIN = 1
PARCELAS_MAX = 48
# Mês da 1ª parcela: do mês da compra (0) até dois meses depois (2).
PRIMEIRA_COMPETENCIA_DESLOCAMENTO_MAX = 2


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


def validar_primeira_competencia(data_compra: date, primeira: date) -> date:
    """Confere o mês da 1ª parcela e devolve o primeiro dia desse mês."""
    mes_compra = Mes.de_data(data_compra)
    mes = Mes.de_data(primeira)
    limite = mes_compra.somar(PRIMEIRA_COMPETENCIA_DESLOCAMENTO_MAX)
    if not mes_compra <= mes <= limite:
        raise DataInvalida(
            f"Mês da 1ª parcela inválido: {mes.abreviado}.",
            sugestao=(
                f"Para uma compra em {mes_compra.abreviado}, use de "
                f"{mes_compra.abreviado} a {limite.abreviado}."
            ),
        )
    return mes.primeiro_dia


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


def gerar_parcelas(
    total: Decimal, n: int, data_compra: date, primeira_competencia: date | None = None
) -> list[ParcelaPlanejada]:
    valores = distribuir(total, n)
    if primeira_competencia is not None:
        primeira_competencia = validar_primeira_competencia(data_compra, primeira_competencia)
    return [
        ParcelaPlanejada(
            numero=k,
            valor=v.quantize(Decimal("0.01")),
            competencia=competencia_parcela(data_compra, k, primeira_competencia),
        )
        for k, v in enumerate(valores, start=1)
    ]
