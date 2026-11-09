"""Categorias (opcionais em lançamentos). Somente admin gerencia."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from financas.clock import Clock
from financas.db.models import Categoria, Membro, TipoCategoria
from financas.domain.erros import NaoEncontrado, OperacaoInvalida
from financas.services import autorizacao


async def listar(s: AsyncSession, *, somente_ativas: bool = False) -> list[Categoria]:
    q = select(Categoria).order_by(func.lower(Categoria.nome))
    if somente_ativas:
        q = q.where(Categoria.ativa.is_(True))
    return list((await s.execute(q)).scalars())


def _nome(nome: str) -> str:
    limpo = " ".join(nome.split())
    if not limpo or len(limpo) > 60:
        raise OperacaoInvalida("Informe um nome de categoria com até 60 caracteres.")
    return limpo


async def _conferir_unico(s: AsyncSession, nome: str, categoria_id: int | None) -> None:
    q = select(Categoria).where(func.lower(Categoria.nome) == nome.lower())
    outra = (await s.execute(q)).scalar_one_or_none()
    if outra is not None and outra.id != categoria_id:
        raise OperacaoInvalida(f"Já existe a categoria '{outra.nome}'.")


async def criar(
    s: AsyncSession, clock: Clock, ator: Membro, nome: str, tipo: TipoCategoria
) -> Categoria:
    autorizacao.exigir_admin(ator)
    nome = _nome(nome)
    await _conferir_unico(s, nome, None)
    agora = clock.agora()
    c = Categoria(nome=nome, tipo=tipo.value, ativa=True, created_at=agora, updated_at=agora)
    s.add(c)
    await s.flush()
    return c


async def atualizar(
    s: AsyncSession,
    clock: Clock,
    ator: Membro,
    categoria_id: int,
    *,
    nome: str,
    tipo: TipoCategoria,
    ativa: bool,
) -> Categoria:
    autorizacao.exigir_admin(ator)
    c = await s.get(Categoria, categoria_id)
    if c is None:
        raise NaoEncontrado("Categoria não encontrada.")
    nome = _nome(nome)
    await _conferir_unico(s, nome, c.id)
    c.nome, c.tipo, c.ativa, c.updated_at = nome, tipo.value, ativa, clock.agora()
    await s.flush()
    return c
