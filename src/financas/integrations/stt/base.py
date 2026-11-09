"""Interface de transcrição de voz (``SpeechToText``)."""

from __future__ import annotations

from typing import Protocol


class ErroTranscricao(Exception):
    """Falha, timeout ou áudio inválido."""


class SpeechToText(Protocol):
    async def transcrever(self, audio: bytes, formato: str = "ogg") -> str:
        """Transcreve o áudio (OGG/Opus do Telegram) para texto em pt-BR."""
        ...
