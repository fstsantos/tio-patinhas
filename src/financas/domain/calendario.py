"""Serviço central de calendário: meses de referência e intervalos semiabertos.

Todo cálculo de mês (28 a 31 dias, anos bissextos, virada de ano) passa por aqui.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from zoneinfo import ZoneInfo

from financas.domain.erros import DataInvalida

FUSO = ZoneInfo("America/Sao_Paulo")

_ABREV = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")
NOMES_MESES = (
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)  # fmt: skip


class SituacaoMes(StrEnum):
    ENCERRADO = "ENCERRADO"
    CORRENTE = "CORRENTE"
    FUTURO = "FUTURO"


@dataclass(frozen=True, order=True, slots=True)
class Mes:
    """Mês civil (ano, mês). Ordenável e imutável."""

    ano: int
    mes: int

    def __post_init__(self) -> None:
        if not 1 <= self.mes <= 12:
            raise DataInvalida(f"Mês inválido: {self.mes}.")
        if not 1 <= self.ano <= 9999:
            raise DataInvalida(f"Ano inválido: {self.ano}.")

    @classmethod
    def de_data(cls, d: date) -> Mes:
        return cls(d.year, d.month)

    @classmethod
    def parse(cls, texto: str) -> Mes:
        """Aceita ``mm/aaaa``."""
        m = re.fullmatch(r"(\d{1,2})/(\d{4})", texto.strip())
        if m is None:
            raise DataInvalida(f"Mês inválido: '{texto}'.", sugestao="Use o formato mm/aaaa.")
        return cls(int(m[2]), int(m[1]))

    def somar(self, meses: int) -> Mes:
        indice = self.ano * 12 + (self.mes - 1) + meses
        return Mes(indice // 12, indice % 12 + 1)

    @property
    def primeiro_dia(self) -> date:
        return date(self.ano, self.mes, 1)

    @property
    def ultimo_dia(self) -> date:
        return date(self.ano, self.mes, self.total_dias)

    @property
    def total_dias(self) -> int:
        return calendar.monthrange(self.ano, self.mes)[1]

    def intervalo(self) -> tuple[date, date]:
        """Intervalo semiaberto ``[primeiro dia, primeiro dia do mês seguinte)``."""
        return self.primeiro_dia, self.somar(1).primeiro_dia

    def contem(self, d: date) -> bool:
        inicio, fim = self.intervalo()
        return inicio <= d < fim

    def situacao(self, hoje: date) -> SituacaoMes:
        atual = Mes.de_data(hoje)
        if self < atual:
            return SituacaoMes.ENCERRADO
        if self > atual:
            return SituacaoMes.FUTURO
        return SituacaoMes.CORRENTE

    def dias_restantes(self, hoje: date) -> int:
        """Dias restantes incluindo hoje; 0 se encerrado; total se futuro."""
        match self.situacao(hoje):
            case SituacaoMes.ENCERRADO:
                return 0
            case SituacaoMes.FUTURO:
                return self.total_dias
            case SituacaoMes.CORRENTE:
                return (self.ultimo_dia - hoje).days + 1

    @property
    def abreviado(self) -> str:
        """Ex.: ``abr/2026``."""
        return f"{_ABREV[self.mes - 1]}/{self.ano}"

    @property
    def por_extenso(self) -> str:
        """Ex.: ``abril de 2026``."""
        return f"{NOMES_MESES[self.mes - 1]} de {self.ano}"

    def __str__(self) -> str:
        return f"{self.mes:02d}/{self.ano}"


def competencia_parcela(
    data_compra: date, numero: int, primeira_competencia: date | None = None
) -> date:
    """Mês de referência da parcela ``numero`` (1..n).

    Sem ``primeira_competencia``, a parcela 1 é do mês seguinte ao da compra (regra
    padrão). Com ela, a parcela 1 é desse mês e as demais seguem mês a mês.
    Retorna o primeiro dia do mês (formato armazenado em ``parcelas.competencia``).
    """
    if numero < 1:
        raise ValueError("número da parcela começa em 1")
    if primeira_competencia is None:
        return Mes.de_data(data_compra).somar(numero).primeiro_dia
    return Mes.de_data(primeira_competencia).somar(numero - 1).primeiro_dia
