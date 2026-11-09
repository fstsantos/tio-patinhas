"""Provedor LLM da Anthropic (opcional, extra ``llm``): propõe comandos de voz.

Usa structured outputs (JSON Schema) para receber JSON válido. A proposta é só uma
sugestão: ``interpretation.interpretador`` a valida com o mesmo parser do texto e o
usuário confirma antes de qualquer registro. Somente a transcrição é enviada — nenhum
dado financeiro ou pessoal do banco.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from financas.integrations.llm.base import ErroLLM

log = logging.getLogger(__name__)

MODELO_PADRAO = "claude-opus-5-5"

ESQUEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reconhecido": {"type": "boolean"},
        "tipo": {"type": "string", "enum": ["debito", "credito", "ganho"]},
        "valor": {"type": "string"},
        "parcelas": {"type": ["integer", "null"]},
        "descricao": {"type": "string"},
        "data": {"type": ["string", "null"]},
    },
    "required": ["reconhecido", "tipo", "valor", "parcelas", "descricao", "data"],
    "additionalProperties": False,
}

SISTEMA = """Você converte a transcrição de um áudio em português do Brasil em um \
lançamento financeiro estruturado para um app de finanças familiares.

Campos:
- tipo: "debito" (despesa à vista), "credito" (compra parcelada no cartão) ou "ganho" (receita).
- valor: o valor TOTAL em reais no formato brasileiro, com vírgula decimal (ex.: "1.200,50"). \
Numa compra parcelada é o total da compra, nunca o valor de uma parcela.
- parcelas: quantidade de parcelas (1 a 48) se for "credito"; null nos outros tipos.
- descricao: descrição curta do que foi pago ou recebido, sem o valor e sem a data.
- data: "AAAA-MM-DD" somente se a pessoa disse a data; caso contrário null.

Não invente nada. Se o tipo, o valor, a descrição ou (no crédito) as parcelas não forem \
ditos com clareza, ou se a fala deixar dúvida entre valor da parcela e valor total, \
responda com reconhecido=false e preencha os demais campos com valores vazios \
("debito", "", null, "", null)."""


class LLMAnthropic:
    def __init__(
        self,
        api_key: str,
        *,
        modelo: str = MODELO_PADRAO,
        timeout: float = 15.0,
        cliente: Any = None,
    ) -> None:
        if cliente is None:
            try:
                import anthropic  # noqa: PLC0415 - dependência opcional (extra 'llm')
            except ImportError as e:  # pragma: no cover
                raise ErroLLM("pacote 'anthropic' não instalado (extra 'llm')") from e
            cliente = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout, max_retries=1)
        self._cliente = cliente
        self.modelo = modelo

    async def propor_comando(self, transcricao: str, hoje: date) -> dict[str, Any] | None:
        import anthropic  # noqa: PLC0415

        try:
            resposta = await self._cliente.beta.messages.create(
                model=self.modelo,
                max_tokens=1024,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": ESQUEMA},
                },
                system=SISTEMA,
                messages=[
                    {
                        "role": "user",
                        "content": f"Hoje é {hoje.isoformat()}.\nTranscrição: {transcricao}",
                    }
                ],
            )
        except anthropic.APITimeoutError as e:
            raise ErroLLM("timeout na LLM") from e
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
            raise ErroLLM(f"falha na LLM: {type(e).__name__}") from e

        if resposta.stop_reason != "end_turn":
            log.info("llm_sem_proposta", extra={"stop_reason": resposta.stop_reason})
            return None
        texto = next((b.text for b in resposta.content if b.type == "text"), None)
        if texto is None:
            return None
        try:
            dados = json.loads(texto)
        except json.JSONDecodeError as e:
            raise ErroLLM("resposta da LLM não é JSON") from e
        if not isinstance(dados, dict) or not dados.get("reconhecido"):
            return None
        return {k: dados.get(k) for k in ("tipo", "valor", "parcelas", "descricao", "data")}
