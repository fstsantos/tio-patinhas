"""Leitor da fatura fechada do Itaú exportada em ``.xlsx``.

Layout esperado (aba única):

- um título ``Fatura Fechada - <Mês>/<ano>``, que define o mês de referência;
- uma célula ``Valor`` com o total da fatura na célula logo abaixo;
- uma linha de cabeçalho com ``Data``, ``Lançamento``, ``Parcelamento`` e ``Valor``;
- uma linha por lançamento até a primeira linha sem data.

O pagamento da fatura anterior ("Pagamento Boleto" etc.) não faz parte do total e é
descartado. A soma das demais linhas precisa bater com o total, senão a leitura falha.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from financas.domain.calendario import NOMES_MESES, Mes
from financas.domain.texto import normalizar

CENTAVO = Decimal("0.01")

# Formato "colunado": nome (23) + cidade (13) + espaço + país (3).
_LARGURA_COLUNADA = 40
_LARGURA_NOME = 23
# Formato "colado": nome + cidade + "bra", sem separadores. Só dá para separar a
# cidade conhecendo-a; as cidades vêm truncadas em 13 caracteres.
CIDADES_PADRAO = (
    "sao paulo", "so paulo", "osasco", "barueri", "guarulhos", "brusque",
    "rio de janeir", "campinas", "santo andre", "sao bernardo", "sao caetano d",
    "santana de pa", "cotia", "carapicuiba", "jandira", "itapevi", "santos",
    "curitiba", "belo horizont", "brasilia", "porto alegre", "florianopolis",
    "salvador", "recife", "fortaleza",
)  # fmt: skip

_TITULO = re.compile(r"fatura\b.*?-\s*([a-z]+)\s*/\s*(\d{4})")
_PARCELA = re.compile(r"parcela\s+(\d+)\s+de\s+(\d+)")
_MESES = {normalizar(nome): i for i, nome in enumerate(NOMES_MESES, start=1)}


class ErroFatura(Exception):
    """A planilha não tem o layout esperado ou os valores não conferem."""


@dataclass(frozen=True, slots=True)
class LinhaFatura:
    linha: int  # linha da planilha
    data: date
    descricao_bruta: str
    descricao: str  # sem cidade e país
    parcela: int  # k de "Parcela k de N"; 1 nas compras à vista
    qtd_parcelas: int
    valor: Decimal  # valor cobrado nesta fatura (negativo em estornos)


@dataclass(frozen=True, slots=True)
class Fatura:
    mes: Mes
    total: Decimal
    linhas: tuple[LinhaFatura, ...]
    pagamentos_descartados: int


def limpar_descricao(bruta: str, cidades: tuple[str, ...] = CIDADES_PADRAO) -> str:
    """Remove cidade e país do fim da descrição do estabelecimento."""
    if len(bruta) == _LARGURA_COLUNADA and bruta.casefold().endswith(" bra"):
        return " ".join(bruta[:_LARGURA_NOME].split())
    texto = " ".join(bruta.split())
    if not texto.casefold().endswith("bra"):
        return texto
    sem_pais = texto[:-3].rstrip()
    baixo = sem_pais.casefold()
    for cidade in sorted(cidades, key=len, reverse=True):
        if baixo.endswith(cidade) and len(baixo) > len(cidade):
            return sem_pais[: -len(cidade)].rstrip(" -") or texto
    return texto


def _texto(valor: Any) -> str:
    return valor.strip() if isinstance(valor, str) else ""


def _data(valor: Any) -> date | None:
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    return None


def _cel(linha: list[Any], j: int) -> Any:
    return linha[j] if j < len(linha) else None


def _dinheiro(valor: Any, onde: str) -> Decimal:
    if isinstance(valor, bool) or not isinstance(valor, int | float | Decimal):
        raise ErroFatura(f"Valor inválido em {onde}: {valor!r}.")
    return Decimal(str(valor)).quantize(CENTAVO)


def _eh_pagamento(descricao: str, valor: Decimal) -> bool:
    return valor < 0 and normalizar(descricao).startswith("pagamento")


def _abrir(caminho: Path) -> list[list[Any]]:
    from openpyxl import load_workbook  # noqa: PLC0415 — só a importação precisa da lib

    try:
        with warnings.catch_warnings():
            # A exportação do Itaú vem sem estilo padrão; o aviso não interessa.
            warnings.simplefilter("ignore", UserWarning)
            planilha = load_workbook(caminho, data_only=True, read_only=True)
    except Exception as e:
        raise ErroFatura(f"Não foi possível abrir a planilha: {e}") from e
    try:
        return [list(linha) for linha in planilha.worksheets[0].iter_rows(values_only=True)]
    finally:
        planilha.close()


def _topo(celulas: list[list[Any]]) -> tuple[Mes, Decimal, int, dict[str, int]]:
    """Mês de referência, total, índice da linha de cabeçalho e colunas por nome."""
    mes: Mes | None = None
    total: Decimal | None = None
    for i, linha in enumerate(celulas):
        nomes = {normalizar(_texto(v)): j for j, v in enumerate(linha) if _texto(v)}
        if {"data", "lancamento", "parcelamento", "valor"} <= nomes.keys():
            if mes is None:
                raise ErroFatura("Título 'Fatura Fechada - <mês>/<ano>' não encontrado.")
            if total is None:
                raise ErroFatura("Total da fatura não encontrado.")
            return mes, total, i, nomes
        for texto, j in nomes.items():
            achado = _TITULO.search(texto)
            if achado and achado[1] in _MESES:
                mes = Mes(int(achado[2]), _MESES[achado[1]])
            if texto == "valor" and i + 1 < len(celulas):
                total = _dinheiro(_cel(celulas[i + 1], j), "total da fatura")
    raise ErroFatura("Cabeçalho (Data, Lançamento, Parcelamento, Valor) não encontrado.")


def _parcelamento(texto: str, numero: int) -> tuple[int, int]:
    if not texto:
        return 1, 1
    achado = _PARCELA.fullmatch(texto)
    if achado is None or not 1 <= int(achado[1]) <= int(achado[2]):
        raise ErroFatura(f"Linha {numero}: parcelamento inválido '{texto}'.")
    return int(achado[1]), int(achado[2])


def ler_fatura(caminho: Path, cidades: tuple[str, ...] = CIDADES_PADRAO) -> Fatura:
    celulas = _abrir(caminho)
    mes, total, inicio, col = _topo(celulas)
    linhas: list[LinhaFatura] = []
    pagamentos = 0
    for i in range(inicio + 1, len(celulas)):
        linha = celulas[i]
        numero = i + 1
        dia = _data(_cel(linha, col["data"]))
        if dia is None:
            break
        bruta = _cel(linha, col["lancamento"])
        if not isinstance(bruta, str) or not bruta.strip():
            raise ErroFatura(f"Linha {numero}: lançamento sem descrição.")
        valor = _dinheiro(_cel(linha, col["valor"]), f"linha {numero}")
        if _eh_pagamento(bruta, valor):
            pagamentos += 1
            continue
        parcela, qtd = _parcelamento(normalizar(_texto(_cel(linha, col["parcelamento"]))), numero)
        linhas.append(
            LinhaFatura(numero, dia, bruta, limpar_descricao(bruta, cidades), parcela, qtd, valor)
        )
    if not linhas:
        raise ErroFatura("Nenhum lançamento encontrado na planilha.")
    soma = sum((linha.valor for linha in linhas), Decimal(0))
    if soma != total:
        raise ErroFatura(f"A soma dos lançamentos ({soma}) não confere com o total ({total}).")
    return Fatura(mes, total, tuple(linhas), pagamentos)
