"""Linha de comando ``financas``."""

from __future__ import annotations

import asyncio
import getpass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated

import typer

from financas.clock import RelogioSistema
from financas.config import get_settings
from financas.db.models import Papel
from financas.db.session import criar_engine, criar_fabrica, transacao
from financas.domain.dinheiro import formatar_brl
from financas.domain.erros import ErroDominio
from financas.integrations.fatura.itau import ErroFatura, Fatura, ler_fatura
from financas.integrations.llm import ErroLLM, criar_llm
from financas.interpretation.interpretador import interpretar_com_llm
from financas.logging import configurar_logs
from financas.services import importacao, membros, recorrencias
from financas.services.importacao import Plano, Regras, Situacao
from financas.services.integridade import Problema, verificar_integridade
from financas.services.membros import DadosMembro

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
    dados = DadosMembro(nome, Papel.ADMIN, True, telegram_id, login, senha)
    try:
        criado = asyncio.run(_criar_admin(dados))
    except ErroDominio as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from e
    typer.secho(f"Administrador '{criado}' criado.", fg=typer.colors.GREEN)


async def _criar_admin(dados: DadosMembro) -> str:
    """Passa pelo serviço de membros: mesmas validações e auditoria da web."""
    engine = criar_engine(get_settings().database_url)
    try:
        async with transacao(criar_fabrica(engine)) as s:
            return (await membros.criar_admin_cli(s, RelogioSistema(), dados)).nome
    finally:
        await engine.dispose()


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


@app.command("gerar-recorrentes")
def gerar_recorrentes() -> None:
    """Lança as recorrências que faltam até o mês corrente (a web já faz isso sozinha)."""
    criados = asyncio.run(_gerar_recorrentes())
    for descricao, data, valor in criados:
        typer.echo(f"{data:%d/%m/%Y}  {descricao}  {formatar_brl(valor)}")
    typer.secho(f"{len(criados)} lançamento(s) gerado(s).", fg=typer.colors.GREEN)


async def _gerar_recorrentes() -> list[tuple[str, date, Decimal]]:
    engine = criar_engine(get_settings().database_url)
    try:
        async with transacao(criar_fabrica(engine)) as s:
            criados = await recorrencias.gerar(s, RelogioSistema())
            return [(c.descricao, c.data_operacao, c.valor_total) for c in criados]
    finally:
        await engine.dispose()


@app.command("importar-fatura")
def importar_fatura(
    arquivo: Annotated[Path, typer.Argument(help="Fatura fechada do Itaú (.xlsx)")],
    membro: Annotated[str, typer.Option(help="Nome do membro titular das compras")],
    regras: Annotated[
        Path | None, typer.Option(help="TOML com trechos a ignorar e categorias")
    ] = None,
    confirmar: Annotated[
        bool, typer.Option("--confirmar", help="Grava; sem isso, só mostra a prévia")
    ] = False,
) -> None:
    """Importa uma fatura de cartão como compras no crédito. Prévia por padrão."""
    try:
        fatura = ler_fatura(arquivo)
        regras_ = importacao.carregar_regras(regras) if regras else Regras()
        plano, criados = asyncio.run(_importar(fatura, membro, regras_, confirmar))
    except (ErroFatura, ErroDominio) as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from e
    _mostrar_plano(plano)
    if plano.com(Situacao.ERRO):
        typer.secho("Há linhas com erro; nada foi importado.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    if confirmar:
        typer.secho(f"{criados} compra(s) importada(s).", fg=typer.colors.GREEN)
    else:
        typer.echo("Prévia: nada foi gravado. Repita com --confirmar para importar.")


async def _importar(
    fatura: Fatura, membro: str, regras: Regras, confirmar: bool
) -> tuple[Plano, int]:
    engine = criar_engine(get_settings().database_url)
    clock = RelogioSistema()
    try:
        async with transacao(criar_fabrica(engine)) as s:
            titular = await importacao.membro_por_nome(s, membro)
            plano = await importacao.planejar(s, titular, fatura, regras)
            if not confirmar or plano.com(Situacao.ERRO):
                return plano, 0
            return plano, len(await importacao.executar(s, clock, plano))
    finally:
        await engine.dispose()


def _mostrar_plano(plano: Plano) -> None:
    fatura = plano.fatura
    typer.echo(
        f"Fatura {fatura.mes.abreviado} — total {formatar_brl(fatura.total)} — "
        f"titular {plano.titular.nome}"
    )
    for item in plano.itens:
        linha = item.linha
        parcelas = f"{linha.parcela}/{linha.qtd_parcelas}" if linha.qtd_parcelas > 1 else "à vista"
        complemento = item.detalhe or item.categoria or "sem categoria"
        if item.situacao is Situacao.EXISTENTE:
            complemento = f"compra {item.detalhe}"
        typer.echo(
            f"{linha.linha:>4}  {linha.data:%d/%m/%Y}  {linha.descricao[:40]:<40}  "
            f"{parcelas:>7}  {formatar_brl(linha.valor):>14}  "
            f"{item.situacao.value:<9}  {complemento}"
        )
    typer.echo(
        f"Novas: {len(plano.com(Situacao.NOVA))} ({formatar_brl(plano.total(Situacao.NOVA))}) · "
        f"já existentes: {len(plano.com(Situacao.EXISTENTE))} "
        f"({formatar_brl(plano.total(Situacao.EXISTENTE))}) · "
        f"ignoradas: {len(plano.com(Situacao.IGNORADA))} "
        f"({formatar_brl(plano.total(Situacao.IGNORADA))}) · "
        f"erros: {len(plano.com(Situacao.ERRO))}"
    )
    no_mes = plano.total(Situacao.NOVA, Situacao.EXISTENTE)
    typer.echo(f"Parcelas desta fatura em {fatura.mes.abreviado}: {formatar_brl(no_mes)}")


@app.command("testar-llm")
def testar_llm(
    frase: Annotated[str, typer.Argument(help="Texto como se fosse a transcrição de um áudio")],
) -> None:
    """Envia uma frase direto à LLM configurada e mostra o que ela entendeu.

    Serve para validar provedor, chave e modelo. Não grava nada. Aqui a LLM é sempre
    chamada; no bot ela só entra quando a interpretação local falha.
    """
    cfg = get_settings()
    llm = criar_llm(cfg)
    if llm is None:
        typer.secho("Nenhuma LLM configurada (LLM_PROVEDOR=nenhum).", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    hoje = RelogioSistema().hoje()
    try:
        comandos, duvida = asyncio.run(
            interpretar_com_llm(frase, hoje, llm, cfg.llm_timeout_segundos)
        )
    except ErroLLM as e:
        typer.secho(f"Falha: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from e
    typer.echo(f"Provedor: {cfg.llm_provedor.value} · modelo: {getattr(llm, 'modelo', '-')}")
    for n, cmd in enumerate(comandos, start=1):
        typer.echo(f"{n}. {cmd}")
    if duvida:
        typer.echo(f"Dúvida: {duvida}")
    if not comandos and not duvida:
        typer.echo("A LLM não propôs nenhum lançamento válido.")


if __name__ == "__main__":  # pragma: no cover
    app()
