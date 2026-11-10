"""Interpretação de transcrições de voz (spec 4).

Fluxo, do mais barato para o mais caro:
1. números falados normalizados → mesmo parser do texto;
2. extração heurística determinística (tudo local, sem custo);
3. só se as etapas locais falharem — ou se o áudio parecer ter mais de um lançamento —
   e houver LLM configurada: proposta da LLM, validada item a item pelo mesmo parser.

Campos ausentes ou ambíguos viram pergunta; nada é inventado. Se a LLM falhar, vale o
resultado local. Todo lançamento por voz exige confirmação antes de ser registrado.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, replace
from datetime import date
from typing import Any

from financas.domain.dinheiro import parece_valor
from financas.domain.erros import ErroDominio
from financas.domain.texto import normalizar
from financas.integrations.llm.base import ErroLLM, LLMProvider, propor_lancamentos
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
    # Vários lançamentos no mesmo áudio (só via LLM); ``comando`` fica None.
    lote: tuple[c.Lancamento, ...] = ()


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


def _varios_valores(texto: str) -> bool:
    """Mais de um valor na fala: provavelmente mais de um lançamento no mesmo áudio."""
    valores = [
        t for t in texto.split() if parece_valor(t) and "/" not in t and not _PARCELAS.fullmatch(t)
    ]
    return len(valores) > 1


async def interpretar_com_llm(
    transcricao: str, hoje: date, llm: LLMProvider, timeout: float = 15.0
) -> tuple[tuple[c.Lancamento, ...], str | None]:
    """Lançamentos propostos pela LLM, já validados pelo parser, e a dúvida dela.

    Se qualquer item for inválido, nenhum é aceito: o áudio é tratado por inteiro.
    Levanta ``ErroLLM`` em falha ou timeout.
    """
    try:
        proposta = await asyncio.wait_for(
            propor_lancamentos(llm, transcricao, hoje), timeout=timeout
        )
    except TimeoutError as e:
        raise ErroLLM("timeout na LLM") from e
    if proposta is None:
        return (), None
    comandos: list[c.Lancamento] = []
    for item in proposta.lancamentos:
        texto = _de_proposta(item)
        r = interpretar(texto, hoje) if texto is not None else None
        if not isinstance(r, c.Debito | c.Credito | c.Ganho):
            return (), proposta.duvida
        comandos.append(r)
    return tuple(comandos), proposta.duvida


def _local(normalizado: str, hoje: date) -> tuple[c.Comando | None, str | None]:
    """Etapas 1 e 2: parser de texto e extração heurística. (comando, pergunta)."""
    direto = interpretar(normalizado, hoje)
    if not isinstance(direto, c.ErroComando | c.NaoReconhecido):
        return direto, None
    texto_cmd, pergunta = _extrair(normalizado, hoje)
    if texto_cmd is not None:
        r = interpretar(texto_cmd, hoje)
        if not isinstance(r, c.ErroComando | c.NaoReconhecido):
            return r, None
        if isinstance(r, c.ErroComando):
            pergunta = f"{r.mensagem} {r.sugestao or ''}".strip()
    elif isinstance(direto, c.ErroComando) and pergunta == PERGUNTA_TIPO:
        # O texto começa com um comando conhecido: o erro do parser é mais útil.
        pergunta = f"{direto.mensagem} {direto.sugestao or ''}".strip()
    return None, pergunta


def _inicial_maiuscula(cmd: c.Lancamento) -> c.Lancamento:
    d = cmd.descricao
    return replace(cmd, descricao=d[:1].upper() + d[1:])


async def interpretar_voz(
    transcricao: str,
    hoje: date,
    *,
    llm: LLMProvider | None = None,
    timeout_llm: float = 15.0,
) -> Interpretacao:
    """Interpreta a transcrição. Em lançamentos, a descrição sai com inicial maiúscula."""
    r = await _interpretar_voz(transcricao, hoje, llm=llm, timeout_llm=timeout_llm)
    if isinstance(r.comando, c.Debito | c.Credito | c.Ganho):
        r = replace(r, comando=_inicial_maiuscula(r.comando))
    if r.lote:
        r = replace(r, lote=tuple(_inicial_maiuscula(x) for x in r.lote))
    return r


async def _interpretar_voz(
    transcricao: str,
    hoje: date,
    *,
    llm: LLMProvider | None,
    timeout_llm: float,
) -> Interpretacao:
    normalizado = normalizar_numeros(transcricao)
    if not normalizado.strip():
        return Interpretacao(normalizado, pergunta="Não consegui entender o áudio.")

    local, pergunta = _local(normalizado, hoje)

    # Um lançamento entendido localmente, mas com mais de um valor na fala, pode ter
    # engolido um segundo lançamento na descrição: vale a segunda opinião da LLM.
    suspeito = isinstance(local, c.Debito | c.Credito | c.Ganho) and _varios_valores(normalizado)
    if local is not None and not (suspeito and llm is not None):
        return Interpretacao(normalizado, comando=local)

    # 3: LLM como reserva.
    if llm is not None and not _pergunta_essencial(pergunta):
        try:
            comandos, duvida = await interpretar_com_llm(transcricao, hoje, llm, timeout_llm)
        except ErroLLM as e:
            log.warning("llm_indisponivel", extra={"erro": str(e)})
            comandos, duvida = (), None
        if len(comandos) == 1:
            return Interpretacao(normalizado, comando=comandos[0], via_llm=True)
        if comandos:
            return Interpretacao(normalizado, lote=comandos, via_llm=True)
        pergunta = pergunta or duvida
    if local is not None:
        return Interpretacao(normalizado, comando=local)
    return Interpretacao(normalizado, pergunta=pergunta or PERGUNTA_TIPO)


def _pergunta_essencial(pergunta: str | None) -> bool:
    """Ambiguidade total × parcela não é resolvida pela LLM: exige o usuário."""
    return pergunta == PERGUNTA_TOTAL_OU_PARCELA
