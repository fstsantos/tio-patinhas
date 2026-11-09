"""Comandos de linha de comando contra o banco de teste."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from financas import cli
from financas.config import get_settings
from financas.db.models import Membro
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
    engine.dispose()
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
    assert "já existe" in repetido.output


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
