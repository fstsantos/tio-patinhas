"""Provedor LLM para qualquer serviço com a API "chat completions" no formato da OpenAI.

Serve à própria OpenAI e aos muitos serviços compatíveis (inclusive modelos locais,
como o Ollama): basta trocar ``LLM_BASE_URL``, ``LLM_MODELO`` e ``LLM_API_KEY``.
Usa só ``response_format: json_object`` — o modo JSON mais amplamente suportado — e
descreve o esquema no próprio prompt; a resposta é conferida depois, no interpretador.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from financas.integrations.llm.base import ErroLLM

log = logging.getLogger(__name__)

BASE_URL_PADRAO = "https://api.openai.com/v1"


class LLMOpenAICompativel:
    def __init__(
        self,
        api_key: str | None,
        *,
        modelo: str,
        base_url: str = BASE_URL_PADRAO,
        timeout: float = 15.0,
        transporte: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key  # None: serviço local sem autenticação
        self.modelo = modelo
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._timeout = timeout
        self._transporte = transporte

    async def completar_json(
        self, sistema: str, usuario: str, esquema: dict[str, Any]
    ) -> dict[str, Any] | None:
        instrucoes = (
            f"{sistema}\n\nResponda somente com o objeto JSON, sem texto em volta, "
            f"seguindo este JSON Schema:\n{json.dumps(esquema, ensure_ascii=False)}"
        )
        corpo = {
            "model": self.modelo,
            "messages": [
                {"role": "system", "content": instrucoes},
                {"role": "user", "content": usuario},
            ],
            "response_format": {"type": "json_object"},
        }
        cabecalhos = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transporte
            ) as cliente:
                r = await cliente.post(self._url, json=corpo, headers=cabecalhos)
        except httpx.TimeoutException as e:
            raise ErroLLM("timeout na LLM") from e
        except httpx.HTTPError as e:
            raise ErroLLM(f"falha na LLM: {type(e).__name__}") from e
        if r.status_code >= 400:
            raise ErroLLM(f"falha na LLM: HTTP {r.status_code}")
        try:
            resposta = r.json()
            escolha = resposta["choices"][0]
            texto = escolha["message"]["content"]
        except (ValueError, LookupError, TypeError) as e:
            raise ErroLLM("resposta da LLM fora do formato esperado") from e
        uso = resposta.get("usage") or {}
        log.info(
            "llm_chamada",
            extra={
                "provedor": "openai",
                "modelo": self.modelo,
                "stop_reason": escolha.get("finish_reason"),
                "consumo_entrada": uso.get("prompt_tokens"),
                "consumo_saida": uso.get("completion_tokens"),
            },
        )
        if not isinstance(texto, str) or not texto.strip():
            return None  # recusa ou resposta vazia
        try:
            dados = json.loads(_sem_cerca(texto))
        except json.JSONDecodeError as e:
            raise ErroLLM("resposta da LLM não é JSON") from e
        return dados if isinstance(dados, dict) else None


def _sem_cerca(texto: str) -> str:
    """Alguns modelos embrulham o JSON em ```json ... ```."""
    t = texto.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        t = t.rsplit("```", 1)[0]
    return t.strip()
