"""Consulta de membros. A identidade no bot é o id numérico do Telegram."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from financas.clock import Clock
from financas.db.models import AcaoAuditoria, Membro, Origem, Papel, SessaoWeb
from financas.domain.erros import NaoEncontrado, OperacaoInvalida
from financas.services import auditoria, autorizacao, senhas

_LOGIN = re.compile(r"[A-Za-z0-9._-]{3,60}")


async def por_telegram(s: AsyncSession, telegram_user_id: int) -> Membro | None:
    """Membro vinculado ao id do Telegram (ativo ou não)."""
    r = await s.execute(select(Membro).where(Membro.telegram_user_id == telegram_user_id))
    return r.scalar_one_or_none()


async def ativo_por_telegram(s: AsyncSession, telegram_user_id: int) -> Membro | None:
    m = await por_telegram(s, telegram_user_id)
    return m if m is not None and m.ativo else None


async def listar(s: AsyncSession, *, incluir_inativos: bool = True) -> list[Membro]:
    q = select(Membro).order_by(Membro.nome)
    if not incluir_inativos:
        q = q.where(Membro.ativo.is_(True))
    return list((await s.execute(q)).scalars())


# ------------------------------------------------------------------ administração

ENTIDADE = "membro"


@dataclass(frozen=True, slots=True)
class DadosMembro:
    nome: str
    papel: Papel
    ativo: bool
    telegram_user_id: int | None = None
    login_web: str | None = None
    senha: str | None = None  # nova senha; None mantém a atual


def _foto(m: Membro) -> dict[str, Any]:
    return {
        "id": m.id, "nome": m.nome, "papel": m.papel, "ativo": m.ativo,
        "telegram_user_id": m.telegram_user_id, "login_web": m.login_web,
        "tem_senha": m.senha_hash is not None,
    }  # fmt: skip


def _normalizar(d: DadosMembro) -> DadosMembro:
    nome = " ".join(d.nome.split())
    if not nome or len(nome) > 100:
        raise OperacaoInvalida("Informe um nome com até 100 caracteres.")
    login = (d.login_web or "").strip() or None
    if login is not None and not _LOGIN.fullmatch(login):
        raise OperacaoInvalida("Login: 3 a 60 caracteres (letras, números, ponto, _ ou -).")
    if d.telegram_user_id is not None and d.telegram_user_id <= 0:
        raise OperacaoInvalida("O id do Telegram de uma pessoa é um número positivo.")
    if d.telegram_user_id is None and not (d.papel is Papel.ADMIN and login):
        raise OperacaoInvalida(
            "Informe o id do Telegram. Ele só é opcional para administrador com login web."
        )
    return DadosMembro(nome, d.papel, d.ativo, d.telegram_user_id, login, d.senha or None)


async def _conferir_unicos(s: AsyncSession, d: DadosMembro, membro_id: int | None) -> None:
    if d.telegram_user_id is not None:
        outro = await por_telegram(s, d.telegram_user_id)
        if outro is not None and outro.id != membro_id:
            raise OperacaoInvalida(f"O id do Telegram já pertence a {outro.nome}.")
    if d.login_web is not None:
        q = select(Membro).where(func.lower(Membro.login_web) == d.login_web.lower())
        outro = (await s.execute(q)).scalar_one_or_none()
        if outro is not None and outro.id != membro_id:
            raise OperacaoInvalida(f"O login '{d.login_web}' já está em uso.")


async def criar(s: AsyncSession, clock: Clock, ator: Membro, dados: DadosMembro) -> Membro:
    autorizacao.exigir_admin(ator)
    return await _criar(s, clock, dados, autor_id=ator.id, origem=Origem.WEB)


async def criar_admin_cli(s: AsyncSession, clock: Clock, dados: DadosMembro) -> Membro:
    """``financas criar-admin``: não há ator (é assim que nasce o 1º administrador).

    Mesmas validações e auditoria da criação pela web, com origem ``CLI``.
    """
    if dados.papel is not Papel.ADMIN or not dados.ativo:
        raise OperacaoInvalida("A linha de comando só cria administradores ativos.")
    if not (dados.login_web or "").strip():
        raise OperacaoInvalida("Informe o login web.")
    return await _criar(s, clock, dados, autor_id=None, origem=Origem.CLI)


async def _criar(
    s: AsyncSession, clock: Clock, dados: DadosMembro, *, autor_id: int | None, origem: Origem
) -> Membro:
    d = _normalizar(dados)
    await _conferir_unicos(s, d, None)
    if d.login_web and not d.senha:
        raise OperacaoInvalida("Defina uma senha para o login web.")
    agora = clock.agora()
    m = Membro(
        nome=d.nome,
        papel=d.papel.value,
        ativo=d.ativo,
        telegram_user_id=d.telegram_user_id,
        login_web=d.login_web,
        senha_hash=_hash(d.senha) if d.senha else None,
        created_at=agora,
        updated_at=agora,
    )
    s.add(m)
    await s.flush()
    await auditoria.registrar(
        s,
        clock,
        acao=AcaoAuditoria.CRIACAO,
        entidade=ENTIDADE,
        entidade_id=m.id,
        autor_id=autor_id,
        origem=origem,
        depois=_foto(m),
    )
    return m


async def atualizar(
    s: AsyncSession, clock: Clock, ator: Membro, membro_id: int, dados: DadosMembro
) -> Membro:
    autorizacao.exigir_admin(ator)
    m = await s.get(Membro, membro_id, with_for_update=True)
    if m is None:
        raise NaoEncontrado("Membro não encontrado.")
    d = _normalizar(dados)
    await _conferir_unicos(s, d, m.id)
    deixa_de_ser_admin = m.eh_admin and m.ativo and (d.papel is not Papel.ADMIN or not d.ativo)
    if deixa_de_ser_admin and await _admins_ativos(s) <= 1:
        raise OperacaoInvalida("É preciso manter pelo menos um administrador ativo.")
    if d.login_web and not (d.senha or m.senha_hash):
        raise OperacaoInvalida("Defina uma senha para o login web.")
    antes = _foto(m)
    m.nome, m.papel, m.ativo = d.nome, d.papel.value, d.ativo
    m.telegram_user_id, m.login_web = d.telegram_user_id, d.login_web
    if d.senha:
        m.senha_hash = _hash(d.senha)
    if d.login_web is None:
        m.senha_hash = None
    m.updated_at = clock.agora()
    await s.flush()
    if not m.ativo or d.senha or d.login_web is None:
        await revogar_sessoes(s, clock, m.id)
    await auditoria.registrar(
        s,
        clock,
        acao=AcaoAuditoria.EDICAO,
        entidade=ENTIDADE,
        entidade_id=m.id,
        autor_id=ator.id,
        origem=Origem.WEB,
        antes=antes,
        depois=_foto(m),
    )
    return m


async def _admins_ativos(s: AsyncSession) -> int:
    q = select(func.count()).where(Membro.papel == Papel.ADMIN.value, Membro.ativo.is_(True))
    return int(await s.scalar(q) or 0)


async def revogar_sessoes(s: AsyncSession, clock: Clock, membro_id: int) -> None:
    await s.execute(
        update(SessaoWeb)
        .where(SessaoWeb.membro_id == membro_id, SessaoWeb.revogada_em.is_(None))
        .values(revogada_em=clock.agora())
    )


def _hash(senha: str) -> str:
    try:
        return senhas.gerar_hash(senha)
    except ValueError as e:
        raise OperacaoInvalida(str(e)) from e
