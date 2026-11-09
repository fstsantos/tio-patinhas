"""Transcrição pela API da OpenAI (alternativa; o áudio é enviado a terceiros)."""

from __future__ import annotations

import httpx

from financas.integrations.stt.base import ErroTranscricao

URL = "https://api.openai.com/v1/audio/transcriptions"


class TranscricaoOpenAI:
    def __init__(
        self,
        api_key: str,
        *,
        modelo: str = "gpt-4o-mini-transcribe",
        timeout: float = 60.0,
        cliente: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self.modelo = modelo
        self.timeout = timeout
        self._cliente = cliente

    async def transcrever(self, audio: bytes, formato: str = "ogg") -> str:
        if not audio:
            raise ErroTranscricao("áudio vazio")
        cliente = self._cliente or httpx.AsyncClient(timeout=self.timeout)
        try:
            r = await cliente.post(
                URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                data={"model": self.modelo, "language": "pt", "response_format": "json"},
                files={"file": (f"audio.{formato}", audio, f"audio/{formato}")},
            )
            r.raise_for_status()
            return str(r.json()["text"]).strip()
        except httpx.TimeoutException as e:
            raise ErroTranscricao("timeout na API de transcrição") from e
        except (httpx.HTTPError, KeyError, ValueError) as e:
            raise ErroTranscricao(f"falha na API de transcrição: {type(e).__name__}") from e
        finally:
            if self._cliente is None:
                await cliente.aclose()
