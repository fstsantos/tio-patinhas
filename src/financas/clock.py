"""Relógio injetável: toda noção de "agora" e "hoje" passa por aqui."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Protocol

from financas.domain.calendario import FUSO


class Clock(Protocol):
    def agora(self) -> datetime:
        """Instante atual, com fuso (UTC)."""
        ...

    def hoje(self) -> date:
        """Data de hoje no fuso America/Sao_Paulo."""
        ...


class RelogioSistema:
    def agora(self) -> datetime:
        return datetime.now(UTC)

    def hoje(self) -> date:
        return self.agora().astimezone(FUSO).date()


class RelogioFixo:
    """Relógio para testes; ``avancar`` permite simular passagem de tempo."""

    def __init__(self, instante: datetime) -> None:
        if instante.tzinfo is None:
            raise ValueError("instante precisa ter fuso horário")
        self._instante = instante

    def agora(self) -> datetime:
        return self._instante

    def hoje(self) -> date:
        return self._instante.astimezone(FUSO).date()

    def definir(self, instante: datetime) -> None:
        if instante.tzinfo is None:
            raise ValueError("instante precisa ter fuso horário")
        self._instante = instante

    def avancar(self, **delta: float) -> None:
        self._instante += timedelta(**delta)
