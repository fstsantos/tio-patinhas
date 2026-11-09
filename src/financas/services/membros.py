"""Consulta de membros. A identidade no bot é o id numérico do Telegram."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from financas.db.models import Membro


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
