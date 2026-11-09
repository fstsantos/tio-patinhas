"""faster-whisper real. A decodificação roda se o extra 'voz' estiver instalado;
a transcrição com modelo exige FINANCAS_TESTE_WHISPER=1 (baixa o modelo 'tiny')."""

import io
import os
import wave

import pytest

from financas.integrations.stt.base import ErroTranscricao
from financas.integrations.stt.faster_whisper import FasterWhisper, amostras_pyav, amostras_wav

av = pytest.importorskip("av")
np = pytest.importorskip("numpy")


def _ogg_opus(segundos: float = 1.0) -> bytes:
    buf = io.BytesIO()
    with av.open(buf, "w", format="ogg") as c:
        st = c.add_stream("libopus", rate=48000, layout="mono")
        t = np.arange(int(48000 * segundos)) / 48000
        pcm = (3000 * np.sin(2 * np.pi * 440 * t)).astype(np.int16).reshape(1, -1)
        for i in range(0, pcm.shape[1], 960):
            q = av.AudioFrame.from_ndarray(pcm[:, i : i + 960], format="s16", layout="mono")
            q.rate = 48000
            for pacote in st.encode(q):
                c.mux(pacote)
        for pacote in st.encode(None):
            c.mux(pacote)
    return buf.getvalue()


def _wav(amostras: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x10" * amostras)
    return buf.getvalue()


def test_decodifica_ogg_opus_para_16khz() -> None:
    a = amostras_pyav(_ogg_opus(2.0))
    assert a.dtype == np.float32
    assert 31000 <= a.shape[0] <= 33000  # ~2 s a 16 kHz
    assert float(abs(a).max()) <= 1.0


def test_decodifica_wav() -> None:
    assert amostras_wav(_wav(800)).shape == (800,)


def test_audio_invalido() -> None:
    with pytest.raises(ErroTranscricao):
        amostras_pyav(b"isto nao e audio")


@pytest.mark.skipif(
    os.environ.get("FINANCAS_TESTE_WHISPER") != "1",
    reason="defina FINANCAS_TESTE_WHISPER=1 para baixar e rodar o modelo",
)
async def test_transcricao_real_com_modelo_tiny() -> None:
    texto = await FasterWhisper("tiny").transcrever(_ogg_opus(2.0))
    assert isinstance(texto, str)  # tom puro: o VAD descarta, texto vazio
