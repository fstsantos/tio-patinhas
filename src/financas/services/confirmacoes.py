"""Confirmações pendentes (spec 4.4): voz e cancelamentos.

- Associadas ao membro, ao chat e à mensagem originais; só o autor resolve.
- Expiram em 5 minutos.
- A transição PENDENTE → EXECUTADA/CANCELADA é um UPDATE condicional atômico:
  sob concorrência, apenas uma requisição a vence.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from financas.clock import Clock
from financas.db.models import ConfirmacaoPendente, StatusConfirmacao, TipoConfirmacao

VALIDADE = timedelta(minutes=5)


class Desfecho(StrEnum):
    OK = "OK"
    NAO_ENCONTRADA = "NAO_ENCONTRADA"
    OUTRO_MEMBRO = "OUTRO_MEMBRO"
    EXPIRADA = "EXPIRADA"
    JA_RESOLVIDA = "JA_RESOLVIDA"


@dataclass(frozen=True, slots=True)
class Resolucao:
    desfecho: Desfecho
    confirmacao: ConfirmacaoPendente | None = None


async def criar(
    s: AsyncSession,
    clock: Clock,
    *,
    tipo: TipoConfirmacao,
    membro_id: int,
    chat_id: int,
    message_id: int,
    payload: dict[str, Any],
    lancamento_id: int | None = None,
) -> ConfirmacaoPendente:
    agora = clock.agora()
    conf = ConfirmacaoPendente(
        id=uuid.uuid4(),
        tipo=tipo.value,
        membro_id=membro_id,
        chat_id=chat_id,
        message_id=message_id,
        payload=payload,
        status=StatusConfirmacao.PENDENTE.value,
        expira_em=agora + VALIDADE,
        lancamento_id=lancamento_id,
        created_at=agora,
    )
    s.add(conf)
    await s.flush()
    return conf


async def resolver(
    s: AsyncSession,
    clock: Clock,
    confirmacao_id: uuid.UUID,
    *,
    membro_id: int,
    chat_id: int,
    confirmar: bool,
) -> Resolucao:
    """Tenta a transição atômica; em caso de falha, explica o motivo."""
    agora = clock.agora()
    novo = StatusConfirmacao.EXECUTADA if confirmar else StatusConfirmacao.CANCELADA
    r = await s.execute(
        update(ConfirmacaoPendente)
        .where(
            ConfirmacaoPendente.id == confirmacao_id,
            ConfirmacaoPendente.status == StatusConfirmacao.PENDENTE.value,
            ConfirmacaoPendente.membro_id == membro_id,
            ConfirmacaoPendente.chat_id == chat_id,
            ConfirmacaoPendente.expira_em > agora,
        )
        .values(status=novo.value, resolvida_em=agora)
        .returning(ConfirmacaoPendente.id)
    )
    if r.scalar_one_or_none() is not None:
        conf = await s.get(ConfirmacaoPendente, confirmacao_id, populate_existing=True)
        return Resolucao(Desfecho.OK, conf)

    conf = (
        await s.execute(
            select(ConfirmacaoPendente)
            .where(ConfirmacaoPendente.id == confirmacao_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if conf is None or conf.chat_id != chat_id:
        return Resolucao(Desfecho.NAO_ENCONTRADA)
    if conf.membro_id != membro_id:
        return Resolucao(Desfecho.OUTRO_MEMBRO, conf)
    if conf.status != StatusConfirmacao.PENDENTE.value:
        if conf.status == StatusConfirmacao.EXPIRADA.value:
            return Resolucao(Desfecho.EXPIRADA, conf)
        return Resolucao(Desfecho.JA_RESOLVIDA, conf)
    conf.status = StatusConfirmacao.EXPIRADA.value
    conf.resolvida_em = agora
    await s.flush()
    return Resolucao(Desfecho.EXPIRADA, conf)


async def expirar_vencidas(s: AsyncSession, clock: Clock) -> int:
    """Manutenção: marca como EXPIRADA as pendentes vencidas."""
    r = await s.execute(
        update(ConfirmacaoPendente)
        .where(
            ConfirmacaoPendente.status == StatusConfirmacao.PENDENTE.value,
            ConfirmacaoPendente.expira_em <= clock.agora(),
        )
        .values(status=StatusConfirmacao.EXPIRADA.value, resolvida_em=clock.agora())
    )
    return int(r.rowcount or 0)  # type: ignore[attr-defined]
