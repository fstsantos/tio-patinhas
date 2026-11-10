"""Fábricas de dados de teste."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from itertools import count
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from financas.clock import RelogioFixo
from financas.db.models import Categoria, Membro, Origem, Papel, TipoLancamento
from financas.services import lancamentos as svc
from financas.services.lancamentos import DadosLancamento, LancamentoComParcelas

_seq = count(1000)


def relogio(dia: date, hora: int = 15) -> RelogioFixo:
    """Relógio fixo às ``hora`` UTC (12h em São Paulo para hora=15)."""
    return RelogioFixo(datetime(dia.year, dia.month, dia.day, hora, tzinfo=UTC))


async def membro(
    s: AsyncSession,
    nome: str = "Ana",
    *,
    admin: bool = False,
    ativo: bool = True,
    telegram_id: int | None = None,
) -> Membro:
    m = Membro(
        nome=nome,
        papel=(Papel.ADMIN if admin else Papel.MEMBRO).value,
        ativo=ativo,
        telegram_user_id=telegram_id if telegram_id is not None else next(_seq),
    )
    s.add(m)
    await s.flush()
    return m


async def categoria(s: AsyncSession, nome: str, tipo: str = "DESPESA") -> Categoria:
    c = Categoria(nome=nome, tipo=tipo, ativa=True)
    s.add(c)
    await s.flush()
    return c


async def lancar(
    s: AsyncSession,
    clock: RelogioFixo,
    ator: Membro,
    tipo: TipoLancamento,
    valor: str,
    descricao: str = "teste",
    data: date | None = None,
    parcelas: int | None = None,
    categoria_id: int | None = None,
    origem: Origem = Origem.TELEGRAM_TEXTO,
) -> LancamentoComParcelas:
    dados = DadosLancamento(
        tipo=tipo,
        valor=Decimal(valor),
        descricao=descricao,
        data_operacao=data or clock.hoje(),
        qtd_parcelas=parcelas,
        categoria_id=categoria_id,
    )
    return await svc.criar(s, clock, ator, dados, origem=origem)


def planilha_fatura(
    caminho: Path,
    linhas: list[tuple[date, str, str | None, float]],
    *,
    titulo: str = "Fatura Fechada - Novembro/2026",
    total: float | None = None,
) -> Path:
    """Planilha no layout da fatura fechada do Itaú: (data, lançamento, parcelamento, valor).

    Sem ``total``, usa a soma das linhas que não são pagamento da fatura anterior.
    """
    from openpyxl import Workbook

    if total is None:
        total = round(sum(v for _d, nome, _p, v in linhas if not nome.startswith("Pagamento")), 2)
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws["B8"], ws["B9"], ws["G9"], ws["G10"] = titulo, "Cartão", "Valor", total
    ws["B13"] = "Lançamentos"
    ws.append([None, "Data", "Lançamento", "Parcelamento", "Valor", None, "Titularidade"])
    for dia, nome, parcelamento, valor in linhas:
        ws.append([None, datetime(dia.year, dia.month, dia.day), nome, parcelamento, valor])
    ws.append([])
    ws.append([None, None, None, "Subtotal  "])
    wb.save(caminho)
    return caminho
