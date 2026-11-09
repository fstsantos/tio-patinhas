"""Transcrição local com faster-whisper (padrão; o áudio não sai da máquina).

Requer o extra ``voz`` (``uv sync --extra voz``). O modelo é carregado sob demanda
na primeira transcrição e reaproveitado.

Com ffmpeg no PATH (padrão no Docker), o áudio vira WAV 16 kHz mono e é entregue ao
modelo como array de amostras, sem passar pelo PyAV. Sem ffmpeg, o arquivo
é decodificado aqui com PyAV (dependência do faster-whisper) e reamostrado para 16 kHz.
O ``decode_audio`` do próprio faster-whisper não é usado: ele é incompatível com PyAV 15+.
"""

from __future__ import annotations

import asyncio
import io
import threading
import wave
from typing import Any

from financas.integrations.stt.audio import converter_para_wav, ffmpeg_disponivel
from financas.integrations.stt.base import ErroTranscricao


class FasterWhisper:
    def __init__(
        self, modelo: str = "small", *, device: str = "cpu", compute_type: str = "int8"
    ) -> None:
        self.nome_modelo = modelo
        self.device = device
        self.compute_type = compute_type
        self._modelo: Any = None
        self._trava = threading.Lock()

    def _carregar(self) -> Any:
        with self._trava:
            if self._modelo is None:
                try:
                    from faster_whisper import WhisperModel  # noqa: PLC0415
                except ImportError as e:  # pragma: no cover - depende do extra
                    raise ErroTranscricao("faster-whisper não instalado (extra 'voz')") from e
                self._modelo = WhisperModel(
                    self.nome_modelo, device=self.device, compute_type=self.compute_type
                )
            return self._modelo

    def _transcrever_sync(self, dados: bytes) -> str:
        modelo = self._carregar()
        entrada = amostras_wav(dados) if dados[:4] == b"RIFF" else amostras_pyav(dados)
        try:
            segmentos, _info = modelo.transcribe(
                entrada, language="pt", vad_filter=True, beam_size=5
            )
            return " ".join(seg.text.strip() for seg in segmentos).strip()
        except Exception as e:
            raise ErroTranscricao(f"falha no faster-whisper: {type(e).__name__}") from e

    async def transcrever(self, audio: bytes, formato: str = "ogg") -> str:
        if not audio:
            raise ErroTranscricao("áudio vazio")
        dados = await converter_para_wav(audio) if ffmpeg_disponivel() else audio
        return await asyncio.to_thread(self._transcrever_sync, dados)


def amostras_wav(dados: bytes) -> Any:
    """WAV PCM 16 bits mono 16 kHz → ``numpy.float32`` em [-1, 1]."""
    import numpy as np  # noqa: PLC0415 - dependência do extra 'voz'

    with wave.open(io.BytesIO(dados)) as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1 or w.getframerate() != 16000:
            raise ErroTranscricao("WAV deve ser PCM 16 bits, mono, 16 kHz")
        quadros = w.readframes(w.getnframes())
    return np.frombuffer(quadros, dtype="<i2").astype(np.float32) / 32768.0


def amostras_pyav(dados: bytes) -> Any:
    """Qualquer formato suportado pelo PyAV (OGG/Opus do Telegram) → float32 16 kHz mono."""
    import av  # noqa: PLC0415 - dependência do extra 'voz'
    import numpy as np  # noqa: PLC0415

    partes = []
    try:
        with av.open(io.BytesIO(dados), mode="r") as conteiner:
            reamostrador = av.AudioResampler(format="s16", layout="mono", rate=16000)
            for quadro in conteiner.decode(audio=0):
                partes += [q.to_ndarray() for q in reamostrador.resample(quadro)]
            partes += [q.to_ndarray() for q in reamostrador.resample(None)]
    except av.error.FFmpegError as e:
        raise ErroTranscricao(f"áudio inválido: {type(e).__name__}") from e
    if not partes:
        raise ErroTranscricao("áudio sem amostras")
    return np.concatenate(partes, axis=1).flatten().astype(np.float32) / 32768.0
