"""Provedores de LLM, escolhidos por configuração (LLM_PROVEDOR).

Para acrescentar um provedor: crie uma classe com ``completar_json`` (ver
``base.LLMProvider``), inclua o nome em ``config.ProvedorLLM`` e um ``case`` aqui.
"""

from __future__ import annotations

from financas.config import ProvedorLLM, Settings
from financas.integrations.llm.base import ErroLLM, LLMFalsa, LLMProvider

__all__ = ["ErroLLM", "LLMProvider", "criar_llm"]


def criar_llm(cfg: Settings) -> LLMProvider | None:
    chave = cfg.llm_api_key.get_secret_value() if cfg.llm_api_key else None
    match cfg.llm_provedor:
        case ProvedorLLM.NENHUM:
            return None
        case ProvedorLLM.FALSO:
            return LLMFalsa()
        case ProvedorLLM.ANTHROPIC:
            from financas.integrations.llm.anthropic import (  # noqa: PLC0415
                MODELO_PADRAO,
                LLMAnthropic,
            )

            if chave is None and cfg.anthropic_api_key is not None:
                chave = cfg.anthropic_api_key.get_secret_value()
            if chave is None:
                raise SystemExit("LLM_PROVEDOR=anthropic exige LLM_API_KEY (ou ANTHROPIC_API_KEY)")
            return LLMAnthropic(
                chave, modelo=cfg.llm_modelo or MODELO_PADRAO, timeout=cfg.llm_timeout_segundos
            )
        case ProvedorLLM.OPENAI:
            from financas.integrations.llm.openai_compativel import (  # noqa: PLC0415
                BASE_URL_PADRAO,
                LLMOpenAICompativel,
            )

            if cfg.llm_modelo is None:
                raise SystemExit("LLM_PROVEDOR=openai exige LLM_MODELO")
            base_url = cfg.llm_base_url or BASE_URL_PADRAO
            if chave is None and cfg.openai_api_key is not None and base_url == BASE_URL_PADRAO:
                # A chave da OpenAI só vai para a própria OpenAI, nunca para outra URL.
                chave = cfg.openai_api_key.get_secret_value()
            if chave is None and base_url == BASE_URL_PADRAO:
                raise SystemExit("LLM_PROVEDOR=openai exige LLM_API_KEY (ou OPENAI_API_KEY)")
            return LLMOpenAICompativel(
                chave, modelo=cfg.llm_modelo, base_url=base_url, timeout=cfg.llm_timeout_segundos
            )
