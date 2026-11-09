import httpx
import pytest
from pydantic import SecretStr

from financas.config import ProvedorSTT, Settings
from financas.integrations.stt import audio as audio_mod
from financas.integrations.stt import criar_stt
from financas.integrations.stt.base import ErroTranscricao
from financas.integrations.stt.faster_whisper import FasterWhisper
from financas.integrations.stt.openai import TranscricaoOpenAI


def _cliente(handler) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_openai_sucesso() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["Authorization"] == "Bearer chave"
        assert b"gpt-4o-mini-transcribe" in req.content
        return httpx.Response(200, json={"text": " débito dez reais "})

    stt = TranscricaoOpenAI("chave", cliente=_cliente(handler))
    assert await stt.transcrever(b"ogg") == "débito dez reais"


@pytest.mark.parametrize(
    "resposta",
    [httpx.Response(500), httpx.Response(200, json={"x": 1}), httpx.Response(401)],
)
async def test_openai_falhas(resposta: httpx.Response) -> None:
    stt = TranscricaoOpenAI("chave", cliente=_cliente(lambda _r: resposta))
    with pytest.raises(ErroTranscricao):
        await stt.transcrever(b"ogg")


async def test_openai_timeout() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("lento", request=req)

    with pytest.raises(ErroTranscricao, match="timeout"):
        await TranscricaoOpenAI("k", cliente=_cliente(handler)).transcrever(b"ogg")


async def test_audio_vazio() -> None:
    with pytest.raises(ErroTranscricao):
        await TranscricaoOpenAI("k").transcrever(b"")
    with pytest.raises(ErroTranscricao):
        await FasterWhisper().transcrever(b"")


async def test_ffmpeg_ausente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audio_mod.shutil, "which", lambda _n: None)
    with pytest.raises(ErroTranscricao, match="ffmpeg"):
        await audio_mod.converter_para_wav(b"x")


def test_fabrica() -> None:
    base = Settings(_env_file=None)  # type: ignore[call-arg]
    assert isinstance(criar_stt(base), FasterWhisper)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        criar_stt(base.model_copy(update={"stt_provedor": ProvedorSTT.OPENAI}))
    cfg = base.model_copy(
        update={"stt_provedor": ProvedorSTT.OPENAI, "openai_api_key": SecretStr("k")}
    )
    assert isinstance(criar_stt(cfg), TranscricaoOpenAI)
    assert criar_stt(base.model_copy(update={"stt_provedor": ProvedorSTT.FALSO})) is not None
