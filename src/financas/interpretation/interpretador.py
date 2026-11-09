"""Interpretação de transcrições de voz (spec 4).

Fluxo: números falados normalizados → mesmo parser do texto → extração heurística
determinística → (opcional) proposta da LLM validada pelo mesmo parser.
Campos ausentes ou ambíguos viram pergunta; nada é inventado. Todo resultado de voz
exige confirmação do usuário antes de ser registrado.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from financas.domain.dinheiro import parece_valor
from financas.domain.erros import ErroDominio
from financas.domain.texto import normalizar
from financas.integrations.llm.base import ErroLLM, LLMProvider
from financas.interpretation import comandos as c
from financas.interpretation.numeros_falados_ptbr import normalizar_numeros
from financas.interpretation.parser_texto import interpretar, parse_data

log = logging.getLogger(__name__)

_CREDITO = {"credito", "parcelado", "parcelada", "parcelei", "parcelou"}
_DEBITO = {"debito", "despesa", "gasto", "gastei", "paguei", "pago", "gastou", "pagou"}
_GANHO = {"ganho", "ganhei", "receita", "recebi", "recebimento", "ganhou", "recebeu"}
_DESCARTAVEIS = {
    "de", "do", "da", "dos", "das", "no", "na", "nos", "nas", "em", "com", "para", "pra",
    "por", "um", "uma", "o", "a", "os", "as", "e", "reais", "real", "centavos", "foi",
    "valor", "total", "rs", "r$", "favor", "registra", "registrar", "registre", "lanca",
    "lancar", "lance", "anota", "anotar", "anote", "adiciona", "adicionar", "coloca",
    "colocar", "bota", "botar", "cartao", "vezes", "parcelas",
} | _CREDITO | _DEBITO | _GANHO  # fmt: skip
_PARCELAS = re.compile(r"(\d{1,3})x", re.IGNORECASE)

PERGUNTA_TIPO = "É um débito (à vista), um crédito (parcelado) ou um ganho?"
PERGUNTA_TOTAL_OU_PARCELA = (
    "O valor informado é o de cada parcela ou o total da compra? "
    "Diga o valor total, por exemplo: crédito de mil reais em dez vezes."
)


@dataclass(frozen=True, slots=True)
class Interpretacao:
    texto_normalizado: str
    comando: c.Comando | None = None
    pergunta: str | None = None
    via_llm: bool = False


def _tipo(tokens_norm: list[str], tem_parcelas: bool) -> str | None:
    achados = {
        nome
        for nome, palavras in (("credito", _CREDITO), ("debito", _DEBITO), ("ganho", _GANHO))
        if any(t in palavras for t in tokens_norm)
    }
    if len(achados) > 1:
        return None
    if achados:
        return achados.pop()
    return "credito" if tem_parcelas else None


class _Pergunta(Exception):
    pass


def _indice_valor(tokens: list[str], idx_parcelas: list[int]) -> int:
    idx = [
        i
        for i, t in enumerate(tokens)
        if i not in idx_parcelas and parece_valor(t) and "/" not in t
    ]
    if not idx:
        raise _Pergunta("Não identifiquei o valor. Qual foi o valor?")
    if len(idx) > 1:
        raise _Pergunta("Identifiquei mais de um valor. Qual é o valor total?")
    return idx[0]


def _parcelas(tipo: str, norm: list[str], idx_parcelas: list[int], iv: int) -> int | None:
    if tipo != "credito":
        if idx_parcelas:
            raise _Pergunta(f"{PERGUNTA_TIPO} Você mencionou parcelas.")
        return None
    if not idx_parcelas:
        raise _Pergunta("Em quantas parcelas foi a compra?")
    if len(idx_parcelas) > 1:
        raise _Pergunta("Identifiquei mais de uma quantidade de parcelas. Quantas foram?")
    ip = idx_parcelas[0]
    if ip < iv and all(t == "de" for t in norm[ip + 1 : iv]):
        raise _Pergunta(PERGUNTA_TOTAL_OU_PARCELA)
    return ip


def _data(tokens: list[str], hoje: date) -> tuple[int | None, date | None]:
    achada: tuple[int | None, date | None] = (None, None)
    for i, t in enumerate(tokens):
        try:
            d = parse_data(t, hoje)
        except ErroDominio as e:
            raise _Pergunta(f"Não entendi a data '{t}'. Qual é a data?") from e
        if d is not None:
            achada = (i, d)
    return achada


def _aparar(partes: list[tuple[str, str]]) -> list[tuple[str, str]]:
    inicio, fim = 0, len(partes)
    while inicio < fim and partes[inicio][1] in _DESCARTAVEIS:
        inicio += 1
    while fim > inicio and partes[fim - 1][1] in _DESCARTAVEIS:
        fim -= 1
    return partes[inicio:fim]


def _descricao(tokens: list[str], norm: list[str], removidos: set[int | None]) -> str:
    resto = [
        (t, n) for i, (t, n) in enumerate(zip(tokens, norm, strict=True)) if i not in removidos
    ]
    # Descrição: o que vem depois da palavra de tipo; senão, o que vem antes.
    pos_tipo = max(
        (k for k, (_, n) in enumerate(resto) if n in _CREDITO | _DEBITO | _GANHO), default=-1
    )
    for partes in (resto[pos_tipo + 1 :], resto[: max(pos_tipo, 0)]):
        aparadas = _aparar(partes)
        if aparadas:
            return " ".join(t for t, _ in aparadas)
    raise _Pergunta("Qual é a descrição do lançamento?")


def _extrair(texto: str, hoje: date) -> tuple[str | None, str | None]:
    """Monta o comando canônico em texto. Retorna (texto_comando, pergunta)."""
    tokens = texto.split()
    norm = [normalizar(t) for t in tokens]
    idx_parcelas = [i for i, t in enumerate(tokens) if _PARCELAS.fullmatch(t)]
    tipo = _tipo(norm, bool(idx_parcelas))
    if tipo is None:
        return None, PERGUNTA_TIPO
    try:
        iv = _indice_valor(tokens, idx_parcelas)
        ip = _parcelas(tipo, norm, idx_parcelas, iv)
        idx_data, data = _data(tokens, hoje)
        descricao = _descricao(tokens, norm, {iv, idx_data, *idx_parcelas})
    except _Pergunta as p:
        return None, str(p)
    partes = [tipo, tokens[iv]]
    if ip is not None:
        partes.append(tokens[ip])
    partes.append(descricao)
    if data is not None:
        partes.append(f"{data:%d/%m/%Y}")
    return " ".join(partes), None


def _de_proposta(dados: dict[str, Any]) -> str | None:
    try:
        tipo = str(dados["tipo"])
        if tipo not in {"debito", "credito", "ganho"}:
            return None
        partes = [tipo, str(dados["valor"]).strip()]
        if tipo == "credito":
            partes.append(f"{int(dados['parcelas'])}x")
        partes.append(str(dados["descricao"]).strip())
        if dados.get("data"):
            partes.append(f"{date.fromisoformat(str(dados['data'])):%d/%m/%Y}")
        return " ".join(partes)
    except (KeyError, TypeError, ValueError):
        return None


async def interpretar_voz(
    transcricao: str,
    hoje: date,
    *,
    llm: LLMProvider | None = None,
    timeout_llm: float = 15.0,
) -> Interpretacao:
    normalizado = normalizar_numeros(transcricao)
    if not normalizado.strip():
        return Interpretacao(normalizado, pergunta="Não consegui entender o áudio.")

    direto = interpretar(normalizado, hoje)
    if not isinstance(direto, c.ErroComando | c.NaoReconhecido):
        return Interpretacao(normalizado, comando=direto)

    texto_cmd, pergunta = _extrair(normalizado, hoje)
    if texto_cmd is not None:
        r = interpretar(texto_cmd, hoje)
        if not isinstance(r, c.ErroComando | c.NaoReconhecido):
            return Interpretacao(normalizado, comando=r)
        pergunta = (
            f"{r.mensagem} {r.sugestao or ''}".strip() if isinstance(r, c.ErroComando) else pergunta
        )
    elif isinstance(direto, c.ErroComando) and pergunta == PERGUNTA_TIPO:
        # O texto começa com um comando conhecido: o erro do parser é mais útil.
        pergunta = f"{direto.mensagem} {direto.sugestao or ''}".strip()

    if llm is not None and pergunta and not _pergunta_essencial(pergunta):
        try:
            proposta = await asyncio.wait_for(
                llm.propor_comando(transcricao, hoje), timeout=timeout_llm
            )
        except (TimeoutError, ErroLLM) as e:
            log.warning("llm_indisponivel", extra={"erro": type(e).__name__})
            proposta = None
        if proposta:
            texto_llm = _de_proposta(proposta)
            if texto_llm is not None:
                r = interpretar(texto_llm, hoje)
                if isinstance(r, c.Debito | c.Credito | c.Ganho):
                    return Interpretacao(normalizado, comando=r, via_llm=True)
    return Interpretacao(normalizado, pergunta=pergunta or PERGUNTA_TIPO)


def _pergunta_essencial(pergunta: str) -> bool:
    """Ambiguidade total × parcela não é resolvida pela LLM: exige o usuário."""
    return pergunta == PERGUNTA_TOTAL_OU_PARCELA
