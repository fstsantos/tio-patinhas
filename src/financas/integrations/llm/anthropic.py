"""Provedor LLM da Anthropic (extra ``llm``), com structured outputs (JSON Schema)."""

from __future__ import annotations

import json
import logging
from typing import Any

from financas.integrations.llm.base import ErroLLM

log = logging.getLogger(__name__)

# Extrair lançamentos de uma frase curta não pede um modelo grande; troque em LLM_MODELO.
MODELO_PADRAO = "claude-haiku-5-5"


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

    async def completar_json(
        self, sistema: str, usuario: str, esquema: dict[str, Any]
    ) -> dict[str, Any] | None:
        import anthropic  # noqa: PLC0415

        try:
            resposta = await self._cliente.messages.create(
                model=self.modelo,
                max_tokens=4096,
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": esquema},
                },
                system=sistema,
                messages=[{"role": "user", "content": usuario}],
            )
        except anthropic.APITimeoutError as e:
            raise ErroLLM("timeout na LLM") from e
        except anthropic.APIStatusError as e:
            raise ErroLLM(f"falha na LLM: {type(e).__name__} ({e.status_code})") from e
        except anthropic.APIConnectionError as e:
            raise ErroLLM(f"falha na LLM: {type(e).__name__}") from e

        uso = getattr(resposta, "usage", None)
        log.info(
            "llm_chamada",
            extra={
                "provedor": "anthropic",
                "modelo": self.modelo,
                "stop_reason": resposta.stop_reason,
                "consumo_entrada": getattr(uso, "input_tokens", None),
                "consumo_saida": getattr(uso, "output_tokens", None),
            },
        )
        if resposta.stop_reason != "end_turn":  # recusa, corte por max_tokens etc.
            return None
        texto = next((b.text for b in resposta.content if b.type == "text"), None)
        if texto is None:
            return None
        try:
            dados = json.loads(texto)
        except json.JSONDecodeError as e:
            raise ErroLLM("resposta da LLM não é JSON") from e
        return dados if isinstance(dados, dict) else None
