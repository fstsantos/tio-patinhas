"""Interface de provedores de LLM (opcional). Nenhuma regra financeira depende dela.

Na interpretação de voz, a LLM só *propõe* um comando em JSON; a proposta passa pelo
mesmo parser dos comandos de texto e por confirmação humana.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol

# Esquema da proposta de comando (validado em interpretation.interpretador).
ESQUEMA_COMANDO: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tipo": {"enum": ["debito", "credito", "ganho"]},
        "valor": {"type": "string", "description": "valor TOTAL em pt-BR, ex.: 1.200,50"},
        "parcelas": {"type": ["integer", "null"], "minimum": 1, "maximum": 48},
        "descricao": {"type": "string", "maxLength": 120},
        "data": {"type": ["string", "null"], "description": "AAAA-MM-DD ou null para hoje"},
    },
    "required": ["tipo", "valor", "descricao"],
    "additionalProperties": False,
}


class ErroLLM(Exception):
    """Falha, timeout ou resposta inválida do provedor."""


class LLMProvider(Protocol):
    async def propor_comando(self, transcricao: str, hoje: date) -> dict[str, Any] | None:
        """Propõe um comando (conforme ESQUEMA_COMANDO) ou None se não souber."""
        ...


class LLMFalsa:
    """Provedor de teste: devolve respostas pré-definidas ou levanta erro."""

    def __init__(self, resposta: dict[str, Any] | None = None, erro: Exception | None = None):
        self.resposta = resposta
        self.erro = erro
        self.chamadas: list[str] = []

    async def propor_comando(self, transcricao: str, hoje: date) -> dict[str, Any] | None:
        self.chamadas.append(transcricao)
        if self.erro is not None:
            raise self.erro
        return self.resposta
