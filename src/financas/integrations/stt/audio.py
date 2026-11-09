"""Conversão de áudio com ffmpeg (OGG/Opus do Telegram → WAV 16 kHz mono)."""

from __future__ import annotations

import asyncio
import shutil

from financas.integrations.stt.base import ErroTranscricao


def ffmpeg_disponivel() -> bool:
    return shutil.which("ffmpeg") is not None


async def converter_para_wav(audio: bytes, *, timeout: float = 60.0) -> bytes:
    if not ffmpeg_disponivel():
        raise ErroTranscricao("ffmpeg não encontrado no PATH")
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
        "-ac", "1", "-ar", "16000", "-f", "wav", "pipe:1",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )  # fmt: skip
    try:
        saida, erro = await asyncio.wait_for(proc.communicate(audio), timeout=timeout)
    except TimeoutError as e:
        proc.kill()
        raise ErroTranscricao("ffmpeg excedeu o tempo limite") from e
    if proc.returncode != 0 or not saida:
        raise ErroTranscricao(f"ffmpeg falhou: {erro.decode(errors='replace')[:200]}")
    return saida
