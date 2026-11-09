"""Normalização determinística de números falados em pt-BR.

Exemplos::

    "dez reais e cinquenta"      → "10,50"
    "mil e duzentos"             → "1200"
    "cinquenta centavos"         → "0,50"
    "em dez vezes" / "10 vezes"  → "em 10x"
    "vinte e cinco reais"        → "25"

Palavras que não são números são preservadas como foram ditas (com acentos).
O artigo "um/uma" só vira número quando seguido de moeda, "mil" ou "vezes".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from financas.domain.texto import normalizar

_PALAVRAS: dict[str, int] = {
    "zero": 0, "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5,
    "seis": 6, "sete": 7, "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12,
    "treze": 13, "quatorze": 14, "catorze": 14, "quinze": 15, "dezesseis": 16,
    "dezessete": 17, "dezoito": 18, "dezenove": 19, "vinte": 20, "trinta": 30,
    "quarenta": 40, "cinquenta": 50, "sessenta": 60, "setenta": 70, "oitenta": 80,
    "noventa": 90, "cem": 100, "cento": 100, "duzentos": 200, "duzentas": 200,
    "trezentos": 300, "trezentas": 300, "quatrocentos": 400, "quatrocentas": 400,
    "quinhentos": 500, "quinhentas": 500, "seiscentos": 600, "seiscentas": 600,
    "setecentos": 700, "setecentas": 700, "oitocentos": 800, "oitocentas": 800,
    "novecentos": 900, "novecentas": 900,
}  # fmt: skip
_MULTIPLICADORES = {"mil": 1000, "milhao": 1_000_000, "milhoes": 1_000_000}
_MOEDA = {"reais", "real"}
_CENTAVOS = {"centavos", "centavo"}
_VEZES = {"vezes", "vez", "parcelas", "parcela", "x"}
_ARTIGOS = {"um", "uma"}

_INTEIRO = re.compile(r"\d{1,7}")
_PONTUACAO_FINAL = re.compile(r"[.!?;:,]+$")


@dataclass(frozen=True, slots=True)
class _Num:
    valor: int


def _ordem(v: int) -> int:
    """Menor 'casa' ocupada por v: 100 para centenas, 10 para dezenas ≥ 20, 1 para o resto."""
    if v >= 100 and v % 100 == 0:
        return 100
    if v >= 20 and v % 10 == 0:
        return 10
    return 1


def _limpar(token: str) -> str:
    return _PONTUACAO_FINAL.sub("", token)


def _valor_palavra(token: str) -> int | None:
    t = normalizar(token)
    if t in _PALAVRAS:
        return _PALAVRAS[t]
    if _INTEIRO.fullmatch(t):
        return int(t)
    return None


def _ler_numero(tokens: list[str], i: int) -> tuple[int, int] | None:
    """Lê uma expressão numérica a partir de ``i``. Retorna (valor, próximo índice)."""
    total = 0
    grupo = 0
    ultimo: int | None = None
    j = i
    fim = i
    while j < len(tokens):
        t = normalizar(tokens[j])
        if t in _MULTIPLICADORES:
            if fim == i and t != "mil":
                break
            total += (grupo or 1) * _MULTIPLICADORES[t]
            grupo, ultimo = 0, _MULTIPLICADORES[t]
            j += 1
            fim = j
            continue
        if t == "e" and fim > i and j + 1 < len(tokens):
            prox = _valor_palavra(tokens[j + 1])
            if prox is not None and _cabe(prox, ultimo):
                j += 1
                continue
            break
        v = _valor_palavra(tokens[j])
        if v is None:
            break
        if fim > i and not _cabe(v, ultimo):
            break
        grupo += v
        ultimo = v
        j += 1
        fim = j
    if fim == i:
        return None
    return total + grupo, fim


def _cabe(v: int, ultimo: int | None) -> bool:
    if ultimo is None:
        return True
    if ultimo >= 1000:
        return v < 1000
    return v < _ordem(ultimo) and _ordem(ultimo) > 1


def normalizar_numeros(texto: str) -> str:
    tokens = [_limpar(t) for t in texto.split()]
    tokens = [t for t in tokens if t]
    itens: list[str | _Num] = []
    i = 0
    while i < len(tokens):
        lido = _ler_numero(tokens, i)
        if lido is None:
            itens.append(tokens[i])
            i += 1
            continue
        valor, j = lido
        seguinte = normalizar(tokens[j]) if j < len(tokens) else ""
        so_artigo = j == i + 1 and normalizar(tokens[i]) in _ARTIGOS
        if so_artigo and seguinte not in _MOEDA | _VEZES | _CENTAVOS:
            itens.append(tokens[i])
            i += 1
            continue
        itens.append(_Num(valor))
        i = j
    return " ".join(_compor(itens))


def _palavra(item: str | _Num | None) -> str:
    return normalizar(item) if isinstance(item, str) else ""


def _compor(itens: list[str | _Num]) -> list[str]:
    """Combina números com moeda, centavos e "vezes"."""
    saida: list[str] = []
    i = 0
    n = len(itens)

    def item(k: int) -> str | _Num | None:
        return itens[k] if k < n else None

    while i < n:
        atual = itens[i]
        if not isinstance(atual, _Num):
            saida.append(atual)
            i += 1
            continue
        v = atual.valor
        p1, p2, p3 = item(i + 1), item(i + 2), item(i + 3)
        if _palavra(p1) in _MOEDA:
            # N reais [e] M [centavos]
            if _palavra(p2) == "e" and isinstance(p3, _Num) and p3.valor < 100:
                saida.append(f"{v},{p3.valor:02d}")
                i += 4 + (1 if _palavra(item(i + 4)) in _CENTAVOS else 0)
                continue
            if isinstance(p2, _Num) and p2.valor < 100:
                saida.append(f"{v},{p2.valor:02d}")
                i += 3 + (1 if _palavra(item(i + 3)) in _CENTAVOS else 0)
                continue
            saida.append(str(v))
            i += 2
            continue
        if _palavra(p1) in _CENTAVOS and v < 100:
            saida.append(f"0,{v:02d}")
            i += 2
            continue
        if _palavra(p1) in _VEZES:
            saida.append(f"{v}x")
            i += 2
            continue
        if _palavra(p1) == "e" and isinstance(p2, _Num) and p2.valor < 100:
            # "dez e cinquenta": dezena seguida de centavos (não combinável como inteiro)
            saida.append(f"{v},{p2.valor:02d}")
            i += 3 + (1 if _palavra(p3) in _CENTAVOS | _MOEDA else 0)
            continue
        saida.append(str(v))
        i += 1
    return saida
