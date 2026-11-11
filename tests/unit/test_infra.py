import json
import logging
from datetime import UTC, datetime

import pytest

from financas.clock import RelogioFixo
from financas.config import Settings
from financas.logging import FormatadorJSON, FormatadorTexto, mascarar


def test_relogio_fixo_usa_fuso_de_sao_paulo() -> None:
    # 02:00 UTC de 01/04 ainda é 31/03 em São Paulo (UTC-3)
    r = RelogioFixo(datetime(2026, 4, 1, 2, 0, tzinfo=UTC))
    assert r.hoje().isoformat() == "2026-03-31"
    r.avancar(minutes=59)
    assert r.hoje().isoformat() == "2026-03-31"
    r.avancar(minutes=1)
    assert r.hoje().isoformat() == "2026-04-01"


def test_relogio_exige_fuso() -> None:
    with pytest.raises(ValueError, match="fuso"):
        RelogioFixo(datetime(2026, 4, 1))


def test_chats_permitidos_por_virgula(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_CHATS_PERMITIDOS", "-1001234, 42")
    assert Settings(_env_file=None).telegram_chats_permitidos == [-1001234, 42]  # type: ignore[call-arg]


# Segredos fictícios montados em pedaços: escritos por extenso, os scanners de
# segredo (GitHub etc.) os confundem com credenciais reais.
_TOKEN_TELEGRAM_FICTICIO = ":".join(["123456789", "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw0"])
_CHAVE_ANTHROPIC_FICTICIA = "-".join(["sk", "ant", "abcdefghijklmnopqrstuv"])


@pytest.mark.parametrize(
    ("texto", "proibido"),
    [
        (f"token {_TOKEN_TELEGRAM_FICTICIO} enviado", "AAHdqTcv"),
        (f"chave {_CHAVE_ANTHROPIC_FICTICIA}", "abcdefghijk"),
        ("senha=minhasenha123", "minhasenha123"),
        ("postgresql+psycopg://user:segredo@db:5432/x", "segredo"),
    ],
)
def test_mascarar_segredos(texto: str, proibido: str) -> None:
    assert proibido not in mascarar(texto)


def test_formatador_json_mascara_campos_extras() -> None:
    reg = logging.makeLogRecord(
        {"msg": "login %s", "args": ("ok",), "levelname": "INFO", "senha": "abc", "membro_id": 7}
    )
    dados = json.loads(FormatadorJSON().format(reg))
    assert dados["msg"] == "login ok"
    assert dados["senha"] == "***"
    assert dados["membro_id"] == 7


def test_formatador_texto_inclui_campos_extras() -> None:
    reg = logging.makeLogRecord(
        {
            "msg": "lancamento_registrado",
            "levelname": "INFO",
            "name": "bot",
            "lancamento_id": 7,
            "descricao": "café da manhã",
            "parcelas": None,
            "senha": "abc",
        }
    )
    linha = FormatadorTexto("%(levelname)s %(name)s %(message)s").format(reg)
    assert linha == (
        'INFO bot lancamento_registrado lancamento_id=7 descricao="café da manhã" '
        "parcelas=- senha=***"
    )


def test_formatador_texto_sem_extras_nao_muda() -> None:
    reg = logging.makeLogRecord({"msg": "bot_iniciado", "levelname": "INFO", "name": "bot"})
    assert FormatadorTexto("%(levelname)s %(message)s").format(reg) == "INFO bot_iniciado"


def test_variaveis_vazias_sao_ausentes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "  ")
    cfg = Settings(_env_file=None)  # type: ignore[call-arg]
    assert cfg.telegram_bot_token is None and cfg.anthropic_api_key is None
