"""Configuração tipada, lida de variáveis de ambiente ou ``.env``."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Ambiente(StrEnum):
    DESENVOLVIMENTO = "desenvolvimento"
    TESTE = "teste"
    PRODUCAO = "producao"


class ProvedorSTT(StrEnum):
    FASTER_WHISPER = "faster_whisper"
    OPENAI = "openai"
    FALSO = "falso"


class ProvedorLLM(StrEnum):
    NENHUM = "nenhum"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    FALSO = "falso"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ambiente: Ambiente = Ambiente.DESENVOLVIMENTO
    database_url: str = "postgresql+psycopg://financas:financas@localhost:5432/financas"
    log_level: str = "INFO"
    log_json: bool = True

    # Telegram
    telegram_bot_token: SecretStr | None = None
    telegram_bot_username: str | None = None
    telegram_chats_permitidos: Annotated[list[int], NoDecode] = Field(default_factory=list)
    telegram_webhook_url: str | None = None
    telegram_webhook_porta: int = 8443

    # Web
    web_secret_key: SecretStr = SecretStr("troque-esta-chave-em-producao")
    web_sessao_horas: int = 12
    web_host: str = "127.0.0.1"
    web_port: int = 8000

    # Voz
    stt_provedor: ProvedorSTT = ProvedorSTT.FASTER_WHISPER
    stt_modelo_whisper: str = "small"
    stt_modelo_openai: str = "gpt-4o-mini-transcribe"
    stt_timeout_segundos: float = 120.0
    voz_duracao_maxima_segundos: int = 120
    openai_api_key: SecretStr | None = None

    # LLM (opcional)
    llm_provedor: ProvedorLLM = ProvedorLLM.NENHUM
    llm_timeout_segundos: float = 15.0
    llm_modelo: str = "claude-opus-5-5"
    anthropic_api_key: SecretStr | None = None

    @field_validator(
        "telegram_bot_token", "telegram_bot_username", "telegram_webhook_url",
        "openai_api_key", "anthropic_api_key", mode="before",
    )  # fmt: skip
    @classmethod
    def _vazio_e_ausente(cls, v: object) -> object:
        """``CHAVE=`` no .env significa "não configurado", não string vazia."""
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator("telegram_chats_permitidos", mode="before")
    @classmethod
    def _lista_ids(cls, v: object) -> object:
        if isinstance(v, str):
            return [int(p) for p in v.replace(";", ",").split(",") if p.strip()]
        if isinstance(v, int):
            return [v]
        return v

    @property
    def em_producao(self) -> bool:
        return self.ambiente is Ambiente.PRODUCAO


@lru_cache
def get_settings() -> Settings:
    return Settings()
