"""Provedor de transcrição falso, para testes e desenvolvimento sem modelo."""

from __future__ import annotations

from financas.integrations.stt.base import ErroTranscricao


class TranscricaoFalsa:
    def __init__(self, texto: str = "", erro: Exception | None = None) -> None:
        self.texto = texto
        self.erro = erro
        self.chamadas = 0

    async def transcrever(self, audio: bytes, formato: str = "ogg") -> str:
        self.chamadas += 1
        if self.erro is not None:
            raise self.erro
        if not audio:
            raise ErroTranscricao("áudio vazio")
        return self.texto
