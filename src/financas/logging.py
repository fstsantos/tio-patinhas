"""Logs estruturados em JSON, com mascaramento de segredos."""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

# Padrões de segredos mascarados em qualquer mensagem ou campo.
_PADROES_SEGREDO = [
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"),  # token de bot do Telegram
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),  # chaves estilo OpenAI/Anthropic
    re.compile(r"(?i)(password|senha|token|secret|api[_-]?key)(\s*[=:]\s*)(\S+)"),
    re.compile(r"(postgres(?:ql)?(?:\+\w+)?://[^:/\s]+:)([^@\s]+)(@)"),
]
_CHAVES_SENSIVEIS = re.compile(r"(?i)(senha|password|token|secret|api_key|chave|csrf)")

_ATRIBUTOS_PADRAO = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}


def mascarar(texto: str) -> str:
    t = _PADROES_SEGREDO[0].sub("***", texto)
    t = _PADROES_SEGREDO[1].sub("sk-***", t)
    t = _PADROES_SEGREDO[2].sub(r"\1\2***", t)
    return _PADROES_SEGREDO[3].sub(r"\1***\3", t)


def _mascarar_valor(chave: str, valor: Any) -> Any:
    if _CHAVES_SENSIVEIS.search(chave):
        return "***"
    if isinstance(valor, str):
        return mascarar(valor)
    if isinstance(valor, dict):
        return {k: _mascarar_valor(str(k), v) for k, v in valor.items()}
    return valor


class FormatadorJSON(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        dados: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "nivel": record.levelname,
            "logger": record.name,
            "msg": mascarar(record.getMessage()),
        }
        for chave, valor in vars(record).items():
            if chave not in _ATRIBUTOS_PADRAO and not chave.startswith("_"):
                dados[chave] = _mascarar_valor(chave, valor)
        if record.exc_info:
            dados["exc"] = mascarar(self.formatException(record.exc_info))
        return json.dumps(dados, ensure_ascii=False, default=str)


class FormatadorTexto(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return mascarar(super().format(record))


def configurar_logs(nivel: str = "INFO", *, json_: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        FormatadorJSON()
        if json_
        else FormatadorTexto("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    raiz = logging.getLogger()
    raiz.handlers[:] = [handler]
    raiz.setLevel(nivel.upper())
    # httpx registra URLs com o token do bot; mantém em WARNING.
    logging.getLogger("httpx").setLevel(logging.WARNING)
