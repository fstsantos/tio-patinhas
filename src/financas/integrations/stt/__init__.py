"""Provedores de transcrição, escolhidos por configuração (STT_PROVEDOR)."""

from __future__ import annotations

from financas.config import ProvedorSTT, Settings
from financas.integrations.stt.base import ErroTranscricao, SpeechToText

__all__ = ["ErroTranscricao", "SpeechToText", "criar_stt"]


def criar_stt(cfg: Settings) -> SpeechToText:
    match cfg.stt_provedor:
        case ProvedorSTT.FASTER_WHISPER:
            from financas.integrations.stt.faster_whisper import FasterWhisper  # noqa: PLC0415

            return FasterWhisper(cfg.stt_modelo_whisper)
        case ProvedorSTT.OPENAI:
            from financas.integrations.stt.openai import TranscricaoOpenAI  # noqa: PLC0415

            if cfg.openai_api_key is None:
                raise ValueError("STT_PROVEDOR=openai exige OPENAI_API_KEY")
            return TranscricaoOpenAI(
                cfg.openai_api_key.get_secret_value(),
                modelo=cfg.stt_modelo_openai,
                timeout=cfg.stt_timeout_segundos,
            )
        case ProvedorSTT.FALSO:
            from financas.integrations.stt.falso import TranscricaoFalsa  # noqa: PLC0415

            return TranscricaoFalsa("ajuda")
