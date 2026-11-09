"""Comandos estruturados, independentes do Telegram."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from financas.domain.calendario import Mes


@dataclass(frozen=True, slots=True)
class Debito:
    valor: Decimal
    descricao: str
    data: date


@dataclass(frozen=True, slots=True)
class Credito:
    valor_total: Decimal
    parcelas: int
    descricao: str
    data: date


@dataclass(frozen=True, slots=True)
class Ganho:
    valor: Decimal
    descricao: str
    data: date


@dataclass(frozen=True, slots=True)
class ResumoMes:
    mes: Mes | None = None  # None = mês corrente


@dataclass(frozen=True, slots=True)
class ResumoAno:
    ano: int | None = None


@dataclass(frozen=True, slots=True)
class ResumoCredito:
    pass


@dataclass(frozen=True, slots=True)
class Ajuda:
    pass


@dataclass(frozen=True, slots=True)
class Start:
    pass


@dataclass(frozen=True, slots=True)
class Ultimos:
    pass


@dataclass(frozen=True, slots=True)
class Cancelar:
    codigo: int
    motivo: str | None = None


@dataclass(frozen=True, slots=True)
class Desfazer:
    pass


Lancamento = Debito | Credito | Ganho
Comando = (
    Debito
    | Credito
    | Ganho
    | ResumoMes
    | ResumoAno
    | ResumoCredito
    | Ajuda
    | Start
    | Ultimos
    | Cancelar
    | Desfazer
)


@dataclass(frozen=True, slots=True)
class ErroComando:
    """O texto casa com um comando, mas os argumentos são inválidos."""

    comando: str
    mensagem: str
    sugestao: str | None = None


@dataclass(frozen=True, slots=True)
class NaoReconhecido:
    pass


Resultado = Comando | ErroComando | NaoReconhecido


# Serialização (payload de confirmações pendentes, JSON).

_TIPOS: dict[str, type[Debito] | type[Credito] | type[Ganho]] = {
    "debito": Debito, "credito": Credito, "ganho": Ganho,
}  # fmt: skip


def lancamento_para_json(cmd: Lancamento) -> dict[str, Any]:
    nome = {Debito: "debito", Credito: "credito", Ganho: "ganho"}[type(cmd)]
    dados = {k: (str(v) if isinstance(v, Decimal | date) else v) for k, v in asdict(cmd).items()}
    if isinstance(cmd, Debito | Credito | Ganho):
        dados["data"] = cmd.data.isoformat()
    return {"tipo": nome, **dados}


def lancamento_de_json(dados: dict[str, Any]) -> Lancamento:
    d = dict(dados)
    cls = _TIPOS[d.pop("tipo")]
    d["data"] = date.fromisoformat(d["data"])
    if "valor" in d:
        d["valor"] = Decimal(d["valor"])
    if "valor_total" in d:
        d["valor_total"] = Decimal(d["valor_total"])
    return cls(**d)
