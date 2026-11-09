"""Linha de comando ``financas``."""

from __future__ import annotations

import asyncio
import getpass
from typing import Annotated

import typer
from sqlalchemy import func, select

from financas.config import get_settings
from financas.db.models import Membro, Papel
from financas.db.session import criar_engine, criar_fabrica, transacao
from financas.logging import configurar_logs
from financas.services import senhas
from financas.services.integridade import Problema, verificar_integridade

app = typer.Typer(help="Tio Patinhas — gerenciador financeiro familiar.", no_args_is_help=True)


@app.callback()
def _inicio() -> None:
    cfg = get_settings()
    configurar_logs(cfg.log_level, json_=cfg.log_json)


@app.command("criar-admin")
def criar_admin(
    nome: Annotated[str, typer.Option(prompt=True, help="Nome do administrador")],
    login: Annotated[str, typer.Option(prompt=True, help="Login web")],
    telegram_id: Annotated[
        int | None, typer.Option(help="Identificador numérico do Telegram (opcional)")
    ] = None,
) -> None:
    """Cria um administrador. A senha é lida do terminal, sem eco."""
    senha = getpass.getpass("Senha (mín. 10 caracteres): ")
    if senha != getpass.getpass("Confirme a senha: "):
        typer.secho("As senhas não conferem.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    try:
        senha_hash = senhas.gerar_hash(senha)
    except ValueError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from e
    asyncio.run(_criar_admin(nome.strip(), login.strip(), telegram_id, senha_hash))


async def _criar_admin(nome: str, login: str, telegram_id: int | None, senha_hash: str) -> None:
    engine = criar_engine(get_settings().database_url)
    try:
        async with transacao(criar_fabrica(engine)) as s:
            existe = await s.scalar(
                select(func.count()).where(func.lower(Membro.login_web) == login.lower())
            )
            if existe:
                typer.secho(f"O login '{login}' já existe.", fg=typer.colors.RED, err=True)
                raise typer.Exit(1)
            s.add(
                Membro(
                    nome=nome,
                    login_web=login,
                    senha_hash=senha_hash,
                    telegram_user_id=telegram_id,
                    papel=Papel.ADMIN.value,
                    ativo=True,
                )
            )
    finally:
        await engine.dispose()
    typer.secho(f"Administrador '{nome}' criado.", fg=typer.colors.GREEN)


@app.command("verificar-integridade")
def verificar_integridade_cmd() -> None:
    """Confere parcelas × compras. Código de saída 1 se houver problemas."""
    problemas = asyncio.run(_verificar())
    if not problemas:
        typer.secho("Integridade verificada: nenhum problema encontrado.", fg=typer.colors.GREEN)
        return
    for p in problemas:
        typer.secho(f"[{p.regra}] #{p.entidade_id}: {p.detalhe}", fg=typer.colors.RED)
    raise typer.Exit(1)


async def _verificar() -> list[Problema]:
    engine = criar_engine(get_settings().database_url)
    try:
        async with transacao(criar_fabrica(engine)) as s:
            return await verificar_integridade(s)
    finally:
        await engine.dispose()


if __name__ == "__main__":  # pragma: no cover
    app()
