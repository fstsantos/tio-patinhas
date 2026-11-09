"""Idempotência de mensagens do Telegram (spec 5.4).

O par (chat_id, message_id) é gravado com restrição de unicidade na MESMA transação
do lançamento. Um reenvio encontra o registro e recebe a mesma resposta; uma corrida
entre dois reenvios simultâneos faz a segunda transação falhar por unicidade e,
após o rollback, ela lê a resposta da primeira.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from financas.clock import Clock
from financas.db.models import UpdateProcessado


async def buscar(s: AsyncSession, chat_id: int, message_id: int) -> dict[str, Any] | None:
    r = await s.execute(
        select(UpdateProcessado.resultado).where(
            UpdateProcessado.chat_id == chat_id, UpdateProcessado.message_id == message_id
        )
    )
    bruto = r.scalar_one_or_none()
    return None if bruto is None else dict(json.loads(bruto))


async def registrar(
    s: AsyncSession,
    clock: Clock,
    *,
    chat_id: int,
    message_id: int,
    update_id: int | None,
    resultado: dict[str, Any],
    lancamento_id: int | None = None,
) -> None:
    s.add(
        UpdateProcessado(
            chat_id=chat_id,
            message_id=message_id,
            update_id=update_id,
            lancamento_id=lancamento_id,
            resultado=json.dumps(resultado, ensure_ascii=False),
            recebido_em=clock.agora(),
        )
    )
    await s.flush()
