"""Comandos de linha de comando contra o banco de teste."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from financas import cli
from financas.config import get_settings
from financas.db.models import Auditoria, Membro
from financas.db.session import normalizar_url
from financas.services import senhas

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("limpar_banco")]


@pytest.fixture
def runner(banco_migrado: str, monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    monkeypatch.setenv("DATABASE_URL", banco_migrado)
    # Os logs iriam para o stdout capturado (e depois fechado) pelo CliRunner.
    monkeypatch.setattr(cli, "configurar_logs", lambda *_a, **_k: None)
    get_settings.cache_clear()
    yield CliRunner()  # type: ignore[misc]
    get_settings.cache_clear()


def test_criar_admin(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, banco_migrado: str
) -> None:
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: "senha-muito-forte")
    r = runner.invoke(
        cli.app, ["criar-admin", "--nome", "Ana", "--login", "ana", "--telegram-id", "123"]
    )
    assert r.exit_code == 0, r.output
    engine = create_engine(normalizar_url(banco_migrado))
    with Session(engine) as s:
        m = s.execute(select(Membro)).scalar_one()
        reg = s.execute(select(Auditoria)).scalar_one()
    engine.dispose()
    # Passa pelo serviço de membros: fica na auditoria, com origem CLI e sem autor.
    assert (reg.entidade, reg.entidade_id, reg.acao, reg.origem, reg.autor_id) == (
        "membro",
        m.id,
        "CRIACAO",
        "CLI",
        None,
    )
    assert (m.nome, m.login_web, m.papel, m.telegram_user_id, m.ativo) == (
        "Ana",
        "ana",
        "admin",
        123,
        True,
    )
    assert m.senha_hash and senhas.verificar(m.senha_hash, "senha-muito-forte")

    repetido = runner.invoke(cli.app, ["criar-admin", "--nome", "Outra", "--login", "ANA"])
    assert repetido.exit_code == 1
    assert "já está em uso" in repetido.output

    # id do Telegram repetido: mensagem clara, sem traceback.
    mesmo_tg = runner.invoke(
        cli.app, ["criar-admin", "--nome", "Bia", "--login", "bia", "--telegram-id", "123"]
    )
    assert mesmo_tg.exit_code == 1
    assert "já pertence a Ana" in mesmo_tg.output


@pytest.mark.parametrize(
    ("login", "senha", "trecho"),
    [("a b", "senha-muito-forte", "Login:"), ("ana", "curta", "pelo menos 10")],
)
def test_criar_admin_valida_como_a_web(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, login: str, senha: str, trecho: str
) -> None:
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: senha)
    r = runner.invoke(cli.app, ["criar-admin", "--nome", "Ana", "--login", login])
    assert r.exit_code == 1
    assert trecho in r.output


def test_criar_admin_senhas_diferentes(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    respostas = iter(["senha-muito-forte", "senha-diferente!"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda _p: next(respostas))
    r = runner.invoke(cli.app, ["criar-admin", "--nome", "Ana", "--login", "ana"])
    assert r.exit_code == 1
    assert "não conferem" in r.output


def test_verificar_integridade_ok(runner: CliRunner) -> None:
    r = runner.invoke(cli.app, ["verificar-integridade"])
    assert r.exit_code == 0, r.output
    assert "nenhum problema" in r.output


def test_testar_llm(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from financas.integrations.llm.base import ErroLLM, LLMFalsa

    r = runner.invoke(cli.app, ["testar-llm", "débito 10 mercado"])
    assert r.exit_code == 1 and "Nenhuma LLM configurada" in r.output

    dois = {
        "lancamentos": [
            {"tipo": "debito", "valor": "20", "parcelas": None, "descricao": "a", "data": None},
            {"tipo": "ganho", "valor": "15", "parcelas": None, "descricao": "b", "data": None},
        ],
        "duvida": None,
    }
    for llm, codigo, trechos in (
        (LLMFalsa(dois), 0, ["1. Debito(", "2. Ganho(", "Provedor:"]),
        (LLMFalsa({"lancamentos": [], "duvida": "Qual o valor?"}), 0, ["Dúvida: Qual o valor?"]),
        (LLMFalsa(None), 0, ["não propôs nenhum lançamento"]),
        (LLMFalsa(erro=ErroLLM("timeout na LLM")), 1, ["Falha: timeout na LLM"]),
    ):
        monkeypatch.setattr(cli, "criar_llm", lambda _cfg, llm=llm: llm)
        r = runner.invoke(cli.app, ["testar-llm", "um débito de 20 e um ganho de 15"])
        assert r.exit_code == codigo, r.output
        assert all(t in r.output for t in trechos), r.output
