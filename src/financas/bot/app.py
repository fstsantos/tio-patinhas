"""Processo do bot: ``python -m financas.bot.app``.

Polling na execução local; webhook quando ``TELEGRAM_WEBHOOK_URL`` está definido
(nuvem). O mesmo código serve aos dois modos.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from telegram import Update
from telegram.ext import Application, ApplicationBuilder

from financas.bot import handlers
from financas.bot.processador import Processador
from financas.clock import RelogioSistema
from financas.config import ProvedorLLM, Settings, get_settings
from financas.db.session import criar_engine, criar_fabrica
from financas.integrations.llm.base import LLMFalsa, LLMProvider
from financas.integrations.stt import criar_stt
from financas.logging import configurar_logs

log = logging.getLogger(__name__)

ATUALIZACOES = [Update.MESSAGE, Update.EDITED_MESSAGE, Update.CALLBACK_QUERY]


def criar_llm(cfg: Settings) -> LLMProvider | None:
    match cfg.llm_provedor:
        case ProvedorLLM.NENHUM:
            return None
        case ProvedorLLM.FALSO:
            return LLMFalsa()
        case ProvedorLLM.ANTHROPIC:
            if cfg.anthropic_api_key is None:
                raise SystemExit("LLM_PROVEDOR=anthropic exige ANTHROPIC_API_KEY")
            from financas.integrations.llm.anthropic import LLMAnthropic  # noqa: PLC0415

            return LLMAnthropic(
                cfg.anthropic_api_key.get_secret_value(),
                modelo=cfg.llm_modelo,
                timeout=cfg.llm_timeout_segundos,
            )
        case _:
            log.warning("llm_provedor_nao_implementado", extra={"provedor": cfg.llm_provedor})
            return None


def criar_aplicacao(cfg: Settings) -> Application[Any, Any, Any, Any, Any, Any]:
    if cfg.telegram_bot_token is None:
        raise SystemExit("Defina TELEGRAM_BOT_TOKEN no .env")
    engine = criar_engine(cfg.database_url)
    processador = Processador(
        criar_fabrica(engine),
        RelogioSistema(),
        chats_permitidos=set(cfg.telegram_chats_permitidos),
        bot_username=cfg.telegram_bot_username,
        stt=criar_stt(cfg),
        llm=criar_llm(cfg),
        timeout_stt=cfg.stt_timeout_segundos,
        timeout_llm=cfg.llm_timeout_segundos,
    )

    async def ao_encerrar(_app: Application[Any, Any, Any, Any, Any, Any]) -> None:
        await engine.dispose()

    app = (
        ApplicationBuilder()
        .token(cfg.telegram_bot_token.get_secret_value())
        .concurrent_updates(True)
        .post_shutdown(ao_encerrar)
        .build()
    )
    app.bot_data[handlers.CHAVE_PROCESSADOR] = processador
    app.bot_data[handlers.CHAVE_DURACAO_MAXIMA] = cfg.voz_duracao_maxima_segundos
    handlers.registrar(app)
    return app


def main() -> None:  # pragma: no cover - laço de rede
    cfg = get_settings()
    configurar_logs(cfg.log_level, json_=cfg.log_json)
    app = criar_aplicacao(cfg)
    if cfg.telegram_webhook_url:
        assert cfg.telegram_bot_token is not None
        # Caminho e segredo derivados do token: não expõem o token na URL.
        segredo = hashlib.sha256(cfg.telegram_bot_token.get_secret_value().encode()).hexdigest()
        log.info("bot_iniciado", extra={"modo": "webhook"})
        app.run_webhook(
            listen="0.0.0.0",  # noqa: S104 - dentro do contêiner
            port=cfg.telegram_webhook_porta,
            url_path=segredo[:32],
            webhook_url=f"{cfg.telegram_webhook_url.rstrip('/')}/{segredo[:32]}",
            secret_token=segredo[32:64],
            allowed_updates=ATUALIZACOES,
        )
    else:
        log.info("bot_iniciado", extra={"modo": "polling"})
        app.run_polling(allowed_updates=ATUALIZACOES)


if __name__ == "__main__":  # pragma: no cover
    main()
