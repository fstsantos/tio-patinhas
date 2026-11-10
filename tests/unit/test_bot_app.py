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
    with pytest.raises(SystemExit, match="LLM_API_KEY"):
        bot_app.criar_llm(_cfg(llm_provedor=ProvedorLLM.ANTHROPIC))
    antr = bot_app.criar_llm(
        _cfg(llm_provedor=ProvedorLLM.ANTHROPIC, anthropic_api_key=SecretStr("a"))
    )
    assert antr is not None and antr.modelo == "claude-haiku-5-5"  # type: ignore[attr-defined]
    antr = bot_app.criar_llm(
        _cfg(llm_provedor=ProvedorLLM.ANTHROPIC, llm_api_key=SecretStr("a"), llm_modelo="m")
    )
    assert antr is not None and antr.modelo == "m"  # type: ignore[attr-defined]

    aberta = _cfg(llm_provedor=ProvedorLLM.OPENAI)
    with pytest.raises(SystemExit, match="LLM_MODELO"):
        bot_app.criar_llm(aberta)
    with pytest.raises(SystemExit, match="LLM_API_KEY"):
        bot_app.criar_llm(aberta.model_copy(update={"llm_modelo": "m"}))
    oa = bot_app.criar_llm(
        aberta.model_copy(update={"llm_modelo": "m", "openai_api_key": SecretStr("o")})
    )
    assert oa is not None and oa._api_key == "o"  # type: ignore[attr-defined]
    # Outro endereço: sem chave é permitido (modelo local) e a chave da OpenAI não vaza.
    local = bot_app.criar_llm(
        aberta.model_copy(
            update={
                "llm_modelo": "m",
                "llm_base_url": "http://localhost:11434/v1",
                "openai_api_key": SecretStr("o"),
            }
        )
    )
    assert local is not None and local._api_key is None  # type: ignore[attr-defined]
    assert local._url == "http://localhost:11434/v1/chat/completions"  # type: ignore[attr-defined]
