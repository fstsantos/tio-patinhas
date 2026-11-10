"""LLM opcional para interpretar áudios que as regras locais não entenderam.

Trocar de provedor é simples porque tudo o que é específico da tarefa fica aqui: o
prompt (``SISTEMA``), o formato da resposta (``ESQUEMA``) e a conferência do que
voltou (``propor_lancamentos``). Um provedor só precisa saber fazer uma coisa:
receber instruções + texto e devolver um objeto JSON (``LLMProvider.completar_json``).

Nenhuma regra financeira depende da LLM. Ela só *propõe* lançamentos; cada proposta
passa pelo mesmo parser dos comandos de texto e pela confirmação do usuário. Somente
a transcrição e a data de hoje são enviadas — nenhum dado do banco.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

MAX_LANCAMENTOS = 10
_DIAS = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")

_LANCAMENTO: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tipo": {"type": "string", "enum": ["debito", "credito", "ganho"]},
        "valor": {"type": "string"},
        "parcelas": {"type": ["integer", "null"]},
        "descricao": {"type": "string"},
        "data": {"type": ["string", "null"]},
    },
    "required": ["tipo", "valor", "parcelas", "descricao", "data"],
    "additionalProperties": False,
}

ESQUEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "lancamentos": {"type": "array", "items": _LANCAMENTO},
        "duvida": {"type": ["string", "null"]},
    },
    "required": ["lancamentos", "duvida"],
    "additionalProperties": False,
}

SISTEMA = """Você converte a transcrição de um áudio em português do Brasil em \
lançamentos financeiros para um app de finanças familiares. A transcrição vem de \
reconhecimento de fala e pode ter erros, saudações e palavras soltas: ignore o que não \
for lançamento.

Responda com um objeto JSON com dois campos:
- "lancamentos": lista com um item para cada lançamento dito no áudio (pode haver vários).
- "duvida": null, ou uma pergunta curta em português quando faltar informação.

Campos de cada lançamento:
- "tipo": "debito" (despesa à vista), "credito" (compra parcelada no cartão) ou "ganho" \
(receita, salário, valor recebido).
- "valor": o valor TOTAL em reais no formato brasileiro, com vírgula decimal e ponto de \
milhar (ex.: "1.200,50"). "4.321 e 50 centavos" é "4.321,50". Numa compra parcelada é o \
total da compra, nunca o valor de uma parcela.
- "parcelas": quantidade de parcelas (1 a 48) se for "credito"; null nos outros tipos.
- "descricao": descrição curta do que foi pago ou recebido, sem o valor e sem a data.
- "data": "AAAA-MM-DD" somente se a pessoa disse a data ("ontem", "dia 15", \
"15 de novembro"); caso contrário null. Dia sem mês é do mês corrente; mês sem ano é do \
ano corrente. Datas futuras são válidas.

Não invente nada. Se em algum lançamento o tipo, o valor, a descrição ou (no crédito) as \
parcelas não foram ditos com clareza, ou se a fala deixa dúvida entre valor da parcela e \
valor total, devolva "lancamentos": [] e escreva em "duvida" o que falta. Se o áudio não \
tem nenhum lançamento, devolva "lancamentos": [] e explique em "duvida"."""


class ErroLLM(Exception):
    """Falha, timeout ou resposta inválida do provedor."""


class LLMProvider(Protocol):
    async def completar_json(
        self, sistema: str, usuario: str, esquema: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Devolve o objeto JSON da resposta, ou None se o modelo não respondeu.

        Levanta ``ErroLLM`` em falha de rede, timeout ou resposta que não é JSON.
        """
        ...


@dataclass(frozen=True, slots=True)
class Proposta:
    lancamentos: tuple[dict[str, Any], ...]
    duvida: str | None = None


def mensagem_usuario(transcricao: str, hoje: date) -> str:
    return f"Hoje é {hoje.isoformat()} ({_DIAS[hoje.weekday()]}).\nTranscrição: {transcricao}"


async def propor_lancamentos(llm: LLMProvider, transcricao: str, hoje: date) -> Proposta | None:
    """Pede a proposta ao provedor e confere o formato. None = sem proposta utilizável."""
    dados = await llm.completar_json(SISTEMA, mensagem_usuario(transcricao, hoje), ESQUEMA)
    if not isinstance(dados, dict):
        return None
    itens = dados.get("lancamentos")
    duvida = dados.get("duvida")
    if not isinstance(itens, list) or not all(isinstance(i, dict) for i in itens):
        return None
    if len(itens) > MAX_LANCAMENTOS:
        return None
    texto = " ".join(duvida.split())[:300] if isinstance(duvida, str) else ""
    return Proposta(tuple(itens), texto or None)


class LLMFalsa:
    """Provedor de teste: devolve uma resposta pré-definida ou levanta erro."""

    def __init__(self, resposta: dict[str, Any] | None = None, erro: Exception | None = None):
        self.resposta = resposta
        self.erro = erro
        self.chamadas: list[str] = []

    async def completar_json(
        self, sistema: str, usuario: str, esquema: dict[str, Any]
    ) -> dict[str, Any] | None:
        self.chamadas.append(usuario)
        if self.erro is not None:
            raise self.erro
        return self.resposta
