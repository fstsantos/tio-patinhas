"""Migrações aplicadas e revertidas; integridade garantida pelo banco."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from financas.db.models import Base
from financas.db.session import normalizar_url
from tests.conftest import alembic_config

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("limpar_banco")]


def test_upgrade_downgrade_upgrade(banco_migrado: str) -> None:
    cfg = alembic_config(banco_migrado)
    engine = create_engine(normalizar_url(banco_migrado))
    try:
        command.downgrade(cfg, "base")
        with engine.connect() as con:
            assert set(inspect(con).get_table_names()) <= {"alembic_version"}
            assert (
                con.execute(
                    text("SELECT count(*) FROM pg_proc WHERE proname LIKE 'financas_%'")
                ).scalar()
                == 0
            )
        command.upgrade(cfg, "head")
        with engine.connect() as con:
            tabelas = set(inspect(con).get_table_names())
        assert set(Base.metadata.tables) <= tabelas
    finally:
        engine.dispose()


def test_modelos_coincidem_com_migracoes(banco_migrado: str) -> None:
    """Nenhuma diferença entre os modelos ORM e o esquema migrado (autogenerate vazio)."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    engine = create_engine(normalizar_url(banco_migrado))
    try:
        with engine.connect() as con:
            diff = compare_metadata(MigrationContext.configure(con), Base.metadata)
        assert diff == []
    finally:
        engine.dispose()


async def _membro(s: AsyncSession) -> int:
    r = await s.execute(
        text("INSERT INTO membros (nome, papel) VALUES ('Ana', 'membro') RETURNING id")
    )
    return int(r.scalar_one())


async def _credito(s: AsyncSession, membro: int, total: str, qtd: int) -> int:
    r = await s.execute(
        text(
            "INSERT INTO lancamentos (tipo, valor_total, descricao, descricao_normalizada,"
            " membro_id, criado_por, data_operacao, qtd_parcelas, status, origem)"
            " VALUES ('CREDITO', :t, 'tv', 'tv', :m, :m, :d, :q, 'ATIVO', 'WEB') RETURNING id"
        ),
        {"t": Decimal(total), "m": membro, "d": date(2026, 3, 20), "q": qtd},
    )
    return int(r.scalar_one())


async def _parcela(
    s: AsyncSession,
    lanc: int,
    numero: int,
    valor: str,
    competencia: date,
    status: str = "PENDENTE",
    revisao: int = 1,
) -> None:
    await s.execute(
        text(
            "INSERT INTO parcelas (lancamento_id, revisao_lancamento, numero, valor,"
            " competencia, status) VALUES (:l, :r, :n, :v, :c, :s)"
        ),
        {"l": lanc, "r": revisao, "n": numero, "v": Decimal(valor), "c": competencia, "s": status},
    )


async def test_compra_consistente_e_aceita(fabrica: async_sessionmaker[AsyncSession]) -> None:
    async with fabrica() as s, s.begin():
        m = await _membro(s)
        lanc = await _credito(s, m, "100.00", 3)
        await _parcela(s, lanc, 1, "33.34", date(2026, 4, 1))
        await _parcela(s, lanc, 2, "33.33", date(2026, 5, 1))
        await _parcela(s, lanc, 3, "33.33", date(2026, 6, 1))


async def test_soma_divergente_rejeitada_no_commit(
    fabrica: async_sessionmaker[AsyncSession],
) -> None:
    with pytest.raises(DBAPIError, match="soma das parcelas"):
        async with fabrica() as s, s.begin():
            m = await _membro(s)
            lanc = await _credito(s, m, "100.00", 2)
            await _parcela(s, lanc, 1, "50.00", date(2026, 4, 1))
            await _parcela(s, lanc, 2, "49.99", date(2026, 5, 1))


async def test_compra_sem_parcelas_rejeitada(fabrica: async_sessionmaker[AsyncSession]) -> None:
    with pytest.raises(DBAPIError, match="inconsistentes"):
        async with fabrica() as s, s.begin():
            m = await _membro(s)
            await _credito(s, m, "100.00", 2)


async def test_cancelado_com_parcela_pendente_rejeitado(
    fabrica: async_sessionmaker[AsyncSession],
) -> None:
    async with fabrica() as s, s.begin():
        m = await _membro(s)
        lanc = await _credito(s, m, "10.00", 1)
        await _parcela(s, lanc, 1, "10.00", date(2026, 4, 1))
    with pytest.raises(DBAPIError, match="cancelada com parcelas pendentes"):
        async with fabrica() as s, s.begin():
            await s.execute(
                text("UPDATE lancamentos SET status='CANCELADO', cancelado_em=now() WHERE id=:id"),
                {"id": lanc},
            )
    # cancelamento correto: lançamento e parcelas na mesma transação
    async with fabrica() as s, s.begin():
        await s.execute(
            text("UPDATE lancamentos SET status='CANCELADO', cancelado_em=now() WHERE id=:id"),
            {"id": lanc},
        )
        await s.execute(
            text("UPDATE parcelas SET status='CANCELADA' WHERE lancamento_id=:id"), {"id": lanc}
        )


async def test_reparcelamento_com_substituicao(fabrica: async_sessionmaker[AsyncSession]) -> None:
    async with fabrica() as s, s.begin():
        m = await _membro(s)
        lanc = await _credito(s, m, "10.00", 1)
        await _parcela(s, lanc, 1, "10.00", date(2026, 4, 1))
    async with fabrica() as s, s.begin():
        await s.execute(
            text("UPDATE lancamentos SET qtd_parcelas=2, revisao=2 WHERE id=:id"), {"id": lanc}
        )
        await s.execute(
            text("UPDATE parcelas SET status='SUBSTITUIDA' WHERE lancamento_id=:id"), {"id": lanc}
        )
        await _parcela(s, lanc, 1, "5.00", date(2026, 4, 1), revisao=2)
        await _parcela(s, lanc, 2, "5.00", date(2026, 5, 1), revisao=2)


@pytest.mark.parametrize(("tipo", "qtd"), [("DEBITO", None), ("RECEITA", None), ("CREDITO", 1)])
async def test_data_futura_rejeitada(
    fabrica: async_sessionmaker[AsyncSession], tipo: str, qtd: int | None
) -> None:
    async with fabrica() as s, s.begin():
        m = await _membro(s)
    with pytest.raises(IntegrityError, match="data_nao_futura"):
        async with fabrica() as s, s.begin():
            await s.execute(
                text(
                    "INSERT INTO lancamentos (tipo, valor_total, descricao, descricao_normalizada,"
                    " membro_id, criado_por, data_operacao, qtd_parcelas, status, origem)"
                    " VALUES (:t, 10, 'x', 'x', :m, :m, current_date + 2, :q, 'ATIVO', 'WEB')"
                ),
                {"t": tipo, "m": m, "q": qtd},
            )


@pytest.mark.parametrize(
    ("coluna", "valor"),
    [("competencia", date(2026, 4, 2)), ("valor", Decimal("0"))],
)
async def test_checks_de_parcela(
    fabrica: async_sessionmaker[AsyncSession], coluna: str, valor: object
) -> None:
    with pytest.raises(IntegrityError):
        async with fabrica() as s, s.begin():
            m = await _membro(s)
            lanc = await _credito(s, m, "10.00", 1)
            dados = {"competencia": date(2026, 4, 1), "valor": "10.00"} | {coluna: valor}
            await _parcela(s, lanc, 1, str(dados["valor"]), dados["competencia"])  # type: ignore[arg-type]


async def test_auditoria_somente_insercao(fabrica: async_sessionmaker[AsyncSession]) -> None:
    async with fabrica() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO auditoria (entidade, entidade_id, acao, origem)"
                " VALUES ('lancamento', 1, 'CRIACAO', 'WEB')"
            )
        )
    for comando in ("UPDATE auditoria SET motivo='x'", "DELETE FROM auditoria"):
        with pytest.raises(DBAPIError, match="somente de inserção"):
            async with fabrica() as s, s.begin():
                await s.execute(text(comando))


async def test_parcela_sem_lancamento_rejeitada(
    fabrica: async_sessionmaker[AsyncSession],
) -> None:
    with pytest.raises(IntegrityError):
        async with fabrica() as s, s.begin():
            await _parcela(s, 999_999, 1, "10.00", date(2026, 4, 1))
