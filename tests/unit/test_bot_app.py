import pytest
from pydantic import SecretStr

from financas.bot import app as bot_app
from financas.bot import handlers
from financas.config import ProvedorLLM, ProvedorSTT, Settings


def _cfg(**k: object) -> Settings:
    base = Settings(_env_file=None, stt_provedor=ProvedorSTT.FALSO)  # type: ignore[call-arg]
    return base.model_copy(update=k)


def test_exige_token() -> None:
    with pytest.raises(SystemExit, match="TELEGRAM_BOT_TOKEN"):
        bot_app.criar_aplicacao(_cfg())


def test_monta_aplicacao() -> None:
    cfg = _cfg(
        telegram_bot_token=SecretStr("123:abc"),
        telegram_chats_permitidos=[-1],
        voz_duracao_maxima_segundos=30,
    )
    app = bot_app.criar_aplicacao(cfg)
    proc = app.bot_data[handlers.CHAVE_PROCESSADOR]
    assert proc.chats_permitidos == {-1}
    assert app.bot_data[handlers.CHAVE_DURACAO_MAXIMA] == 30
    assert len(app.handlers[0]) == 3


def test_criar_llm() -> None:
    assert bot_app.criar_llm(_cfg()) is None
    assert bot_app.criar_llm(_cfg(llm_provedor=ProvedorLLM.FALSO)) is not None
    assert bot_app.criar_llm(_cfg(llm_provedor=ProvedorLLM.OPENAI)) is None
