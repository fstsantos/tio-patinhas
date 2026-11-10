"""Registros de auditoria (somente inserção) com estado antes/depois."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from financas.clock import Clock
from financas.db.models import AcaoAuditoria, Auditoria, Lancamento, Origem, Parcela

ENTIDADE_LANCAMENTO = "lancamento"


def _json(valor: Any) -> Any:
    if isinstance(valor, Decimal):
        return str(valor)
    if isinstance(valor, date | datetime):
        return valor.isoformat()
    return valor


def foto_lancamento(lanc: Lancamento, parcelas: list[Parcela] | None = None) -> dict[str, Any]:
    """Estado serializável de um lançamento e, opcionalmente, de suas parcelas vigentes."""
    campos = (
        "id", "tipo", "valor_total", "descricao", "categoria_id", "membro_id", "criado_por",
        "data_operacao", "qtd_parcelas", "primeira_competencia", "status", "cancelado_em",
        "cancelado_por", "motivo_cancelamento", "origem", "revisao",
    )  # fmt: skip
    dados = {c: _json(getattr(lanc, c)) for c in campos}
    if parcelas is not None:
        dados["parcelas"] = [
            {
                "numero": p.numero,
                "valor": str(p.valor),
                "competencia": p.competencia.isoformat(),
                "status": p.status,
                "revisao": p.revisao_lancamento,
            }
            for p in parcelas
        ]
    return dados


async def registrar(
    s: AsyncSession,
    clock: Clock,
    *,
    acao: AcaoAuditoria,
    entidade_id: int,
    autor_id: int | None,
    origem: Origem,
    motivo: str | None = None,
    antes: dict[str, Any] | None = None,
    depois: dict[str, Any] | None = None,
    entidade: str = ENTIDADE_LANCAMENTO,
) -> Auditoria:
    reg = Auditoria(
        entidade=entidade,
        entidade_id=entidade_id,
        acao=acao.value,
        autor_id=autor_id,
        origem=origem.value,
        motivo=motivo,
        dados_antes=antes,
        dados_depois=depois,
        created_at=clock.agora(),
    )
    s.add(reg)
    await s.flush()
    return reg


async def historico(
    s: AsyncSession, entidade_id: int, entidade: str = ENTIDADE_LANCAMENTO
) -> list[Auditoria]:
    r = await s.execute(
        select(Auditoria)
        .where(Auditoria.entidade == entidade, Auditoria.entidade_id == entidade_id)
        .order_by(Auditoria.created_at, Auditoria.id)
    )
    return list(r.scalars())
