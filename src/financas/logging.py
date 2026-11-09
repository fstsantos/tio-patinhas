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


def _extras(record: logging.LogRecord) -> dict[str, Any]:
    """Campos passados em ``extra=`` (já mascarados)."""
    return {
        chave: _mascarar_valor(chave, valor)
        for chave, valor in vars(record).items()
        if chave not in _ATRIBUTOS_PADRAO and not chave.startswith("_")
    }


def _valor_texto(valor: Any) -> str:
    if valor is None:
        return "-"
    texto = str(valor)
    if isinstance(valor, str) and (not texto or any(ch.isspace() or ch in '"=' for ch in texto)):
        return json.dumps(texto, ensure_ascii=False)
    return texto


class FormatadorJSON(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        dados: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "nivel": record.levelname,
            "logger": record.name,
            "msg": mascarar(record.getMessage()),
        }
        dados.update(_extras(record))
        if record.exc_info:
            dados["exc"] = mascarar(self.formatException(record.exc_info))
        return json.dumps(dados, ensure_ascii=False, default=str)


class FormatadorTexto(logging.Formatter):
    """Linha legível; os campos de ``extra=`` vão ao final como ``chave=valor``."""

    def format(self, record: logging.LogRecord) -> str:
        texto = super().format(record)
        campos = " ".join(f"{k}={_valor_texto(v)}" for k, v in _extras(record).items())
        if campos:
            primeira, sep, resto = texto.partition("\n")
            texto = f"{primeira} {campos}{sep}{resto}"
        return mascarar(texto)


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
    for nome in ("httpx", "httpx2"):
        logging.getLogger(nome).setLevel(logging.WARNING)
    # uvicorn instala handlers próprios; faz seus logs passarem pelo formatador (e máscara).
    for nome in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(nome)
        lg.handlers[:] = []
        lg.propagate = True
